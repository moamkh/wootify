# Bale PV: confirmed delivery, truthful Retry, and failure-note cleanup

Status: released under the explicitly updated Wootify v7.1.2 tag and deployed
to prod/demo on 2026-10-17 with Chatvand v4.10.1-chatvand.3. See
`docs/deployments/v7.1.2-confirmed-delivery-20261017.md` for verification/backups.
Date: 2026-10-14.

## Production incident

Conversation 2633 is in account 3, inbox 48, Support Bale PV. Zahra's outgoing
voice message 83898 at 2026-10-11 05:38:12 UTC (09:08:12 Tehran) was a 297,600-byte
MP3. The connector could not connect to `next-ws.bale.ai/set-cookie/`; it failed
before storage upload and before a messaging RPC. Wootify retained its failed
outgoing queue row, marked the bubble failed, and created private note 83899.

Chatvand's access log records a successful Retry API request three seconds after
that status update. The old Retry implementation immediately changed the bubble
to sent, cleared its attributes, and scheduled SendReplyJob. For a Channel::Api
inbox that is not a platform resend. Wootify received message_updated; its edit
path ignored the attachment-only message's empty text. There was no successful
Bale mapping for this voice. The neighboring voice 83780 had a successful mapping
and is a separate send, not evidence that 83898 arrived.

A second false-success path existed in the Bale client: SendMessage ACK timeout
was logged as delivered and returned None. The connector then returned a locally
generated RID as if it were proof of acceptance. Neither a generated RID nor
an upload HTTP success proves that the subsequent messaging RPC succeeded.

## Fixed send boundary

- The WebSocket client distinguishes a disconnected socket before dispatch from
  a lost result during/after dispatch. A matched negative RPC response is marked
  as a definitive rejection. Unmatched errors are not treated as negative ACKs.
- Text and document sends require a successful matching unary RPC response. An
  explicit empty response body is valid; None/no response is not. The adapter
  exposes confirmed=true only when the connector reports delivery_confirmed=true.
- ACK timeout/disconnection during dispatch remains unconfirmed; it is never
  reported as sent or blindly repeated. Confirmation means Bale accepted the
  message, not that the recipient has read it.
- Each message part has a stable instance/conversation/message/part-derived
  request RID. This is correlation, not an assumption that Bale guarantees
  idempotency for unconfirmed resends.

## Durable outgoing workflow

`application/messaging/confirmed_outbound_delivery.py` uses existing MessageMapping
JSON columns, with no schema migration. It records the attachment/text plan,
content/reply target, per-part in-flight RIDs, confirmed receipts, and complete.
The earlier Bale module remains a compatibility import; the implementation is
shared with Eitaa's 2026-10-17 parity fix.
The callback commits in-flight state immediately before SendMessage; media is
uploaded before this messaging checkpoint. A confirmed receipt is committed
before awaiting cosmetic typing cleanup or Chatwoot operations.

Chatwoot's existing failed/red state is retained while confirmation is pending.
Each attachment is checked separately; confirmed parts are skipped on Retry,
and sent is set only when all parts have positive confirmations. Missing URLs,
colliding part keys, changed retry content, and missing ACKs cannot manufacture
a successful batch. Reply mapping and the current peer/contact map are preserved.

Temporary failures proven to occur before messaging dispatch retry through the
existing queue, at most ten automatic attempts. Delays are exponential (2, 4,
8...256 seconds), with the recovery worker checking due jobs every ten seconds.
Permanent rejections remain failed. A later deliberate Retry can try again after
the underlying cause is corrected. Unknown dispatch results retain their ledger
and require positive confirmation; they do not consume repeated customer sends.

Matching native outgoing events can reconcile an exact instance, inbox, peer,
and RID. A per-message lock serializes this with failure reporting, including the
overall delivery-timeout handler. A late error cannot overwrite a confirmed send.
If Chatwoot status/note synchronization fails after acceptance, the queue retries
only bookkeeping and does not send the message to Bale again.

## Native Retry contract and coalescing

The companion Chatvand backend patch changes Retry only for Wootify-managed API
inbox callbacks. It sends the original webhook data with event=message_created,
wootify_retry=true and a per-click retry_request_id UUID, without marking sent or
clearing media, error, or reply attributes. No frontend change is needed.

Wootify keys new send jobs by instance/route/conversation/message, independent of
status, preview text, signed URLs, and retry UUID. Existing Bale jobs using the
old full-payload hash are found by original message identity. Failed jobs rearm
only for a new explicit nonempty retry request ID. Consumed request IDs are also
recorded while a job is pending, so delayed webhook duplicates cannot refill its
attempt budget. Rapid clicks share one pending job. Old failed messages are not
automatically replayed on upgrade.

## Failure notes

Failure notes remain private and are tagged with
`content_attributes.wootify_delivery_failure = {instance_key, message_id}`.
Their IDs are persisted in the delivery job; an accepted note with a lost HTTP
response is recovered by exact metadata, not text matching. Long conversations
use bounded page batches and persist the recovery cursor rather than silently
abandoning older notes. Permanent or uncertain platform errors retain terminal
metadata so a failed reporting request retries reporting only, not the send.

After complete confirmation, Wootify removes only that original message's tagged
notes through Chatvand's narrowly scoped cleanup operation, then marks it sent
and clears external_error. An open Chatvand tab receives its existing deleted
placeholder update; the note row is then removed and is absent after refresh.
Other private notes and public messages are never purged. Private cleanup
broadcasts are ignored before Wootify's edit/delete routing.

Historical untagged notes, including 83899, are not automatically deleted by text
guessing. They need an explicitly verified association before repair. No historic
voice or customer message was resent during local regression verification.

## Boundaries and rollout

Bale normally does not echo sends back to their originating session. A lost ACK
or a process crash between dispatch checkpoint and RPC completion can therefore
remain red until positive evidence is available. Absence of an echo is not proof
of non-delivery. This patch deliberately favors avoiding duplicate voices over
an unsafe automatic resend. The current production deployment is one application
worker; existing queue scheduling locks are process-local, not a distributed
multi-worker lease. Scaling send workers needs a durable dispatch claim/lease.

Both the Wootify and companion Chatvand backend changes must be rolled out for
native Retry and physical note cleanup. Sessions, contact identifiers (including
WhatsApp), instance configuration, database data, and Evolution code are unchanged.
No frontend build or database migration is required.

Tests cover strict ACK semantics, typed transport failures, partial multi-file
retries, stable/coalesced queue identity, exhausted/restarted budgets, native-echo
and timeout races, failure-only reporting retries, and scoped note cleanup.
The companion Chatvand document records exact files and its local test limitation:
`docs/fixes/2026-10-14-wootify-explicit-retry-and-failure-note-cleanup.md`.
