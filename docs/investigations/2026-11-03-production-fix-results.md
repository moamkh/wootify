# Production repair results — 3 November 2026

Wootify remains **7.1.2**. Local changes remain uncommitted; no tag was moved,
created or pushed. Production only was updated. No schema migration ran.

## Confirmed case recovery

| Conversation | Result verified from both native receipt mapping and Chatvand |
|---|---|
| WhatsApp 3330 | Missing customer reply delivered as message110966. Existing operator message109321 reconciled to Chatvand `read` from native receipts, without resending it. |
| WhatsApp 2723 | Album envelope settled without generating another customer message. The preserved receipt image delivered as message111348 with one attachment. |
| WhatsApp 2338 | Both reported retained customer texts delivered as messages110912/110972; the older zero-attempt chat head also delivered. The screenshot receipt photo still has no positively identified original upsert/file in the available capture. It is not represented as recovered. |
| Bale 3775 / 3782 | The independently verified duplicate contacts were consolidated into the existing phone-bearing contact. All ten messages from3782 moved into3775, keeping IDs and attachments. Conversation3775 now contains26 messages;3782 is retained as a resolved shell. |

WhatsApp seven-day disappearing messages remain archived in Chatvand. This was
an intentional preservation of CRM history, not an outgoing delivery failure.

## Permanent changes

Evolution's scheduler inspects eligible partition heads before using worker
slots and advances its cursor only over inspected candidates. Album envelopes
are controls; actual images/videos retain independent file/delivery jobs.
Receipts use native-message partitions. Orphan receipts are retained with an
hourly reconciliation lane after24 hours. Edits, encrypted edits, reactions,
revocations and poll updates are isolated by target rather than blocking later
ordinary chat content. Background media preservation rotates and persists retry
backoff, including across restart. No outbound sends were replayed for recovery.

Wootify selects across scoped canonical/verified aliases and stops historical
outbound webhooks from replacing the active inbound route. Chatvand consults an
authenticated identity/policy preflight before creating PV conversations and
serializes creation by native peer. The live per-inbox reopen setting governs
whether a resolved ticket is reopened or a new ticket is created. Open tickets
are reused in both modes. WhatsApp identifiers are preserved.

The audited Bale data repair retains old callback identities through a validated
Wootify conversation redirect. Chatvand's conversation subresource controller
resolves the old shell only when account, inbox, contact and reciprocal repair
markers match. Retired contact aliases stay locally available for old callbacks
but are excluded from new remote contact lookups.

## Deployment incident and preservation checks

The maintenance command initially used `docker compose create`, which traversed
dependencies. Legacy production Postgres/Redis containers had used anonymous
volumes, while the Compose configuration referred to older named volumes. The
command recreated those dependencies and attached the older volumes. Chatvand
and Wootify remained paused during diagnosis; the production volumes were never
deleted, restored over or reset.

The original volumes were located and reattached. They are now explicitly pinned
as external volumes in the active Compose override, preventing this discrepancy
on subsequent deployments. Application updates use `up --no-deps --no-build`.
The repair helper rejects rerunning a completed repair and no longer uses
dependency-traversing `compose create`.

Post-recovery verification checked baseline IDs across accounts, inboxes,
contacts, conversations, messages, attachments, blobs, instances and Wootify
message mappings. All baseline Chatvand message/attachment/conversation IDs
remain. The one intentionally removed contact is the verified duplicate merged
into its surviving contact. One removed Evolution message row was traced to a
successfully processed native revocation; its original Chatvand message remains
as a deletion tombstone. No unexplained baseline record loss was accepted.

Fresh database dumps, Redis snapshots, session/journal backup, pre-repair dumps,
before-snapshots and deployment manifests remain in private server backup
directories. Runtime environment, command, ports, DNS/proxy settings and
application storage mounts match the pre-deployment baseline. Source hashes
were checked against the actual built/running artifacts. All three application
health checks returned200 and all five WhatsApp sessions returned `open`.

## Validation and limits

Wootify/Bale client suite: **647 passed**. Evolution existing focused Linux
checks: **65 passed**, two optional checks skipped. Isolated Redis regressions
and durable journal checks: **13 passed**.
The isolated regressions
cover a backlog larger than40 jobs, blocked tails, restart, new arrivals,
receipt-partition migration, independent target controls, durable media retry
backoff and existing outbound confirmation/idempotency behavior. Chatvand Ruby
syntax and read-only production-backed alias/legacy-link lookup checks passed;
the authenticated production preflight returned the repaired canonical identity
and ticket, excluding the retired contact.

Recovery is not a queue purge. Unmatched receipts, missing originals and other
unresolved work remain inspectable/retryable. Remaining queue depth does not
mean the screenshot messages are still missing: the individual IDs above were
checked after delivery. A photo absent from every retained original capture
requires a targeted history recovery or customer resend; a queue repair cannot
reconstruct missing bytes.
