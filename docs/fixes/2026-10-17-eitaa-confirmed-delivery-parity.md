# Eitaa PV: confirmed delivery, native Retry and failure-note cleanup

Status: released under the explicitly updated Wootify v7.1.2 tag and deployed
to prod/demo on 2026-10-17 with Chatvand v4.10.1-chatvand.3. No frontend build
or migration. Verification/backups: `docs/deployments/v7.1.2-confirmed-delivery-20261017.md`.

## Same user-visible policy as Bale PV

An outgoing message remains failed/red until the platform positively accepts all
of its parts. A generated request ID, uploaded file, empty/unrecognized response,
or missing RPC result is not sufficient. Temporary failures proven to occur before
the final send retry through the existing durable worker (ten automatic attempts).
Permanent failures stay red with their error and native Retry. A fresh explicit
Retry request rearms a failed job; repeated delivery of the same click cannot reset
its retry budget. Explicit Eitaa flood waits are respected as minimum retry delays.

After all parts are confirmed, Wootify removes only the tagged private failure
notes for that original message, clears its external error and marks it sent.
Chatwoot status/note failures retry bookkeeping without repeating platform sends.
Deleting an unsent message cancels its queued retry. Existing delivered multi-part
platform-delete behavior is not expanded by this patch.

The original incident and Chatvand Retry bug are described in
`2026-10-14-bale-confirmed-delivery-and-native-retry.md`. Chatvand's matching
managed-API-inbox Retry and scoped note-cleanup services already cover both Bale
and Eitaa; their detailed source changes are in the Chatvand project's
`docs/fixes/2026-10-14-wootify-explicit-retry-and-failure-note-cleanup.md`.

## Why Eitaa needed platform-specific changes

Eitaa's server assigns a message ID different from the caller's stable random_id.
The adapter previously chose the first updateMessageID even when unrelated to its
request, and accepted less-specific message_id results. The new confirmation path
requires a positive server ID from updateShortSentMessage, an updateMessageID for
the exact request random_id, or a unique outgoing new-message update for the exact
target peer. True/zero/negative IDs, unrelated mappings and ambiguous replies do
not count as delivery.

The transport also previously tried another endpoint after an HTTP/network error
or empty response, including for final messages.sendMessage/sendMedia requests.
That could send a voice or file twice after the first server accepted it but its
response was lost. A task-local final-send guard now prevents this hidden replay.
Connection/pool failures before transmission are retryable. Read/write failures,
unknown HTTP outcomes and unexplained RPC 5xx outcomes are unconfirmed, not sent.
Matched permanent RPC rejections remain terminal; explicit rate-limit rejections
can retry after the server's delay. Session renewal following an explicit expired
token response remains supported, preserving the same request identity.

Uploads, read requests and renewal retain their appropriate endpoint handling.
The final media send still uses its upload host. File-part success alone never
confirms the final message. Local temporary-file cleanup cannot turn a successful
server send into a delivery error.

## Shared workflow and compatibility

`backend/src/wootify/application/messaging/confirmed_outbound_delivery.py` is the
single implementation for Bale and Eitaa. The older Bale module is a compatibility
import, not a second implementation. Both platforms share queue identity, per-part
checkpoints, error classification, native Retry semantics, reporting locks and
tagged note cleanup. Existing JSON columns hold platform-specific delivery state;
no schema migration or historical message replay is performed.

For Eitaa, attachments are sent one by one without a caption. One separate text
caption is sent last. Confirmed media parts are checkpointed immediately and are
not sent again when the caption or a later file fails. The text caption owns the
canonical Chatwoot message mapping so replies and edits address the text, not the
first photo. Media rows retain their batch relationship. This supports any number
of attachments within existing upstream file-size/part limits, not a fixed three.

Legacy Eitaa batches with positively recorded server IDs import those per-part
receipts before retry; completed files are preserved. Existing idempotency-key
hashes are unchanged. Old sends lacking trustworthy receipt records are not
silently assumed to have arrived or replayed.

The import also handles the old worker's crash window after caption acceptance
but before transferring the Chatwoot ID from the first media row. It reuses that
existing confirmed caption mapping rather than inserting a duplicate server ID.
A regression checks that all three original rows survive, no file/caption is
resent, and the existing caption row becomes the canonical parent.

## Lost acknowledgment recovery

Eitaa difference updates can contain updateMessageID with the original random_id
and assigned server ID. The connector emits these as correlation-only events,
before normal native message echoes, and commits them with the normal durable
inbound event/cursor transaction. They do not create contacts or conversations.

The shared workflow accepts only a unique pending request within the correct
instance and inbox, and checks the peer whenever the update identifies one. A
request ID is never compared with a server message ID as if they were identical.
Wrong, ambiguous or uncorrelated native messages cannot confirm a send. A valid
receipt resolves uncertainty without resending. Remaining unsent batch parts can
resume with the same bounded attempt budget; final confirmation cleans the note
and status. Confirmation racing an HTTP error is also handled without undoing the
positive receipt.

If no positive acknowledgment or correlated update arrives, the message remains
red/unconfirmed. Retry cannot blindly repeat that uncertain send, because the
customer may already have it. A permanent authentication failure still requires
reauthentication; a queue cannot guarantee delivery against a permanently rejected
or unresolvable recipient. Confirmation means platform acceptance, not customer read.

## Source areas and verification

- Eitaa adapter/connector: strict confirmations, dispatch checkpoint, transport
  guard, safe/rejected/uncertain error types, preserved token renewal and stable
  request hashes, durable correlation-only updates.
- Shared delivery workflow and Chatwoot bridge: per-part receipts, caption mapping,
  legacy receipt import, terminal reporting-only retries, native confirmation,
  cleanup and sent status only after all parts are accepted.
- Webhook controller: stable message identity and consumed Retry UUIDs for both
  platforms, durable retries and explicit server wait delays, cancellation guards.
- Tests: actual TL envelope transport, token renewal, invalid/unrelated ACKs,
  1/2/5-file batches, caption-only retries, legacy batches, failure-note cleanup,
  failed/red state, uncertain send duplicate prevention and confirmation races.

No production customer message was resent for these local regression tests.
WhatsApp identifiers, contact routing ownership, instance sessions/configuration
and Evolution code are untouched. Current scheduling locks remain process-local;
multi-worker deployment needs a distributed durable dispatch lease.

Local verification: the complete Python suite passed 617 tests (including 57
shared-workflow regressions). Chatvand's six changed/new Ruby files pass syntax
checks and its actual-service isolated harness passes 26 checks. The full Rails
RSpec suite could not run because the local RSpec executable/dependencies are not
installed; the isolated harness is not a substitute for that request-suite run.
