# Tester message reliability hotfix — October 31, 2026

Applied locally and to production at the user's request. Changes are intentionally
uncommitted: no Git commit, push, version bump, or tag was created. Wootify remains
7.1.2; Chatvand remains 4.10.1-chatvand.3; Evolution retains its upstream 2.3.7 and
custom softnu.9 version, with separately named uncommitted hotfix images.

## Wootify

- `application/messaging/polling.py` resolves Eitaa account-wide deletion events
  using same-instance mappings and queued originals. A channel recovery failure
  or ambiguous deletion no longer blocks unrelated private chats. Deletes wait
  behind their own original message. Ambiguous identities remain isolated.
  A verified later revocation durably supersedes its undelivered original,
  retaining the original snapshot until settlement. This prevents an unavailable
  deleted image from blocking later messages in the same chat.
- `application/messaging/chatwoot_bridge.py` supplies validated source timestamps
  separately from insertion time. Confirmed multi-part sends retain their
  attachment association: a native deletion removes its corresponding Chatvand
  part; a full Chatvand deletion removes all confirmed native siblings. Each
  successful native delete is checkpointed, and native deletion echoes are ignored.
- `infrastructure/chatwoot/client.py` adds the scoped partial-deletion request.

## Chatvand and Evolution

Chatvand fixes delivered-status clocks in API inboxes, cancels stale recording
conversion/upload/FileReader callbacks, and reconciles recent message/attachment
updates on reconnect and browser focus. Source timestamps use consistent history
ordering and cursors, including legacy JSON-string metadata; insertion timestamps
and unread tracking remain separate. Its scoped partial-deletion operation keeps
live sibling attachments and creates a tombstone only when nothing remains.

Evolution accepts a native LID without waiting for phone disclosure and reuses
exact identifiers without overwriting shared contact identifiers. Both text and
multipart delivery preserve source time. Its existing disk/Redis queue now keeps
capture order per chat across failed attempts and restarts, independently of retry
eligibility. Media preservation is bounded and coordinated per journal file;
failure codes survive successful recovery. Outbound ACK and retry safeguards stay
in place.

Separate implementation notes live in each project's `docs/fixes` folder.

## Validation and deployment

- Wootify: **642 tests passed**, including revoked-media supersession and recovery
  across a simulated Chatvand outage for both scoped and account-wide deletes.
- Evolution in the production Linux image: **65 tests passed**, 2 optional tests
  skipped; isolated Redis queue suite: **8 tests passed**. The earlier local
  Windows run timed out in one HTTP-stream test; its Linux run passed.
- Actual Chatvand component evaluation verified status display and the late
  cancelled-recording/FileReader race. Frontend lint has no errors, with two
  pre-existing dynamic translation-key warnings.
- Read-only Rails checks covered source ordering, JSON metadata compatibility,
  cursor exclusion and reconnect ID ordering. Synthetic partial-deletion checks
  verified preservation, idempotency and final tombstones without changing real
  messages. The production frontend was compiled from the running image's source
  plus this patch; unrelated local AI/font changes were excluded.
- All patched image sources match the local manifest. The public frontend asset
  matches the new image. Compose environment, commands, restart settings, ports,
  DNS and persistent mounts were compared before and after replacement.
- All three services return healthy responses and all five WhatsApp connections
  report open. No baseline business IDs were missing after deployment.
- The existing Eitaa queue was retained and resumed delivery; all four specifically
  identified tester messages were recovered. The revoked-media follow-up cleared
  the remaining private-message blockage. Final verification found only two
  unsupported historical channel-recovery events queued, with no unattempted
  private messages; these channel events do not block unrelated chats.
- Four verified historical WhatsApp messages received source-time metadata from
  their exact native mappings. Their bodies and insertion times were unchanged;
  no sends, retries or callbacks were triggered by this repair.

Backups are stored on production under
`/opt/wootify-backups/tester-hotfix-20261031`, including verified database dumps,
Redis snapshots, Evolution sessions/journal, Wootify code/state and configuration.
No new schema migration was added. Production data volumes were retained.

The two reported Bale images were already stored and serialized correctly. The
historical browser/network failure could not be established from retained logs;
reconciliation improves recovery from missed UI events. Previously purged voice
attachments were not reconstructed or resent.
