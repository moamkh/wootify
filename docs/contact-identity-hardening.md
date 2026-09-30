# Contact identity hardening (7.0.8)

## Ownership

Wootify must never set, replace, or clear Chatwoot's account-global
`contact.identifier`, including WhatsApp identifiers. Contact POST/PUT/PATCH
requests strip that field at the shared HTTP client boundary. Existing values
are not migrated or cleared. Profile name/phone/avatar updates are separate
and continue under their existing policies.

Routing uses instance-owned conversation/contact mappings. Historical Wootify
identifiers may be read for compatibility and imported into the local map;
they are never written back. Exact phone matches can reuse an existing
WhatsApp contact. Foreign WhatsApp JIDs are not Bale/Telegram/Eitaa destinations.

## Failure recovery

- A persistent `contact_creations` UUID is committed before remote creation.
  Concurrent routes for the same instance, account scope, and peer share it.
- The UUID is an association `source_id` on the Wootify **API inbox**, not a
  contact identifier and not a WhatsApp inbox source ID.
- Chatvand's API contact creation transaction and unique `(inbox_id, source_id)`
  constraint prevent two committed contacts for this same key. After a lost
  response or local mapping failure, the filter endpoint recovers the contact.
- Contact creation no longer blindly retries read timeouts or server errors.
- Mapped contacts are verified. Confirmed 404s invalidate stale local references;
  authorization errors, outages, or malformed responses do not mean absence.
- Legacy/phone searches require exact matches, paginate, and reject ambiguity.
- Conflicting rebindings, peer types, and ambiguous reverse mappings fail closed.
- A saved account/base-URL scope detects changes on subsequently scoped maps.
  Existing unscoped maps acquire their scope after successful verification.
- Contact sync rolls back failed transactions before processing another contact.

## Migration and rollout

Migration `f7a0c02d21b2` adds nullable `contact_mappings.chatwoot_scope` and the
`contact_creations` table. It does not delete existing contacts or mappings.
Back up the Wootify database, apply `alembic upgrade head`, then restart workers
with this code together. Do not run new workers against the old schema.

The 7.0.8 release includes this migration and the contact identity changes.

## Boundaries and tests

This is failure-safe recovery, not a claim of absolute fault tolerance. Keep
database backups: loss of both local mappings and recovery keys can still
require manual reconciliation. An ambiguous account/contact change requires
operator review. Existing wrong mappings or identifiers are not automatically
repaired. Upgraded Chatwoot forks must retain the API-inbox transaction/unique-key
behavior and contact-inbox filter endpoint used here. Endpoint or database
outages deliberately stop resolution instead of guessing or creating duplicates.

`backend/tests/test_contact_identity_faults.py` tests identifier protection,
lost responses, local failure after remote creation, concurrent independent
sessions/routes, deleted contacts, account changes, ambiguous searches, exact
phone reuse, foreign JIDs, and a data-preserving migration. HTTP/concurrency
tests simulate Chatwoot responses; they are not production end-to-end evidence.
