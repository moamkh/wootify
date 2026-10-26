# Eitaa account-scoped phone discovery — v7.1.2 hotfix

## Incident and root cause

Production account 3, support_eitaa inbox 63, conversation 3515, outgoing message
102197 failed on October 26 at 13:16:55 Asia/Tehran. The Chatwoot contact retained
its correct phone and WhatsApp identifier. Wootify had no conversation/contact
mapping for that peer in support-eitaa-pv, although five other Eitaa instances
mapped the same Chatwoot contact to Eitaa user 24709251.

The old resolver inspected saved contacts and the first 100 dialogs and required
a visible exact phone match with an access hash. Its `eitaa_phone_not_found`
therefore meant no eligible match in those lists, not that Eitaa had verified
the phone was unregistered. Logs do not distinguish an unsaved contact, a hidden
phone, or a peer beyond the first page. No final message RPC ran in this failure.

## Changed behavior

File: `backend/src/wootify/plugins/eitaa_pv/connector.py`.

1. Validate normalized phone input; serialize resolution/imports per account.
2. Return a saved exact-phone contact immediately when its access hash is usable.
3. Scan dialogs with validated peer/top-message/date offsets, excluding pinned
   dialogs on later pages. Bound the scan to 20 pages, stop repeated/missing
   cursors, and never fall back to a generic first search hit.
4. If still unresolved, import exactly one `inputPhoneContact` with a fresh
   positive 63-bit client ID and the normalized international phone. This is
   explicitly authorized by the user's request to implement the proposed fix.
   It can add the contact under the fallback name “Eitaa contact” in Eitaa's
   address book; no Chatwoot display name or WhatsApp identifier is modified.
5. Require a unique imported row correlating our client ID to the returned user
   ID, plus a nonzero valid access hash from this authenticated account. A hidden
   returned phone is allowed only with that correlation. A conflicting visible
   phone, ambiguous ID, unrelated user or missing hash fails closed.
6. Cache the verified peer reference only in this runtime. The existing webhook
   handler persists the instance-owned contact and conversation maps on normal
   successful delivery. It does not copy sibling-instance access hashes.
7. Respect `retry_contacts` and distinguish incomplete lookups from terminal
   unresolved-for-this-account errors. Preserve native red failure/Retry behavior.

No message-sending, acknowledgment, queue, frontend or identity-rewrite logic is
changed. No dependency upgrade, schema migration, database restore or historical
message replay is required. Existing configuration/session files remain in place.

## Tests and rollout

`backend/tests/test_eitaa_phone_import.py` covers the incident, exact known phones,
hidden/mismatched phones, response correlation, ambiguity, absent/zero/boolean
hashes, import retry markers, pagination offsets/repeated cursors, incomplete
responses, invalid input and serialized concurrent lookup. Existing
`test_eitaa_pv_phone_routing.py` retains mapping/alias/WhatsApp protection tests
and expects the corrected unresolved error for an empty account lookup.

Targeted tests: 26 passed. Full backend/protocol suite: 637 passed. Deployment
checks are recorded in the deployment report after verification. No customer message is sent by
these tests. A live production validation, if performed while the connector is
stopped, is limited to resolving/importing the tester's requested contact and
prints no credentials, access hash or unrelated contact details.

## Limits

Eitaa can still refuse discovery/import because of privacy settings, an
unregistered phone, server errors, revoked sessions or rate limits. We cannot
bypass those restrictions or safely guess an identity. The error tells the
operator to check discoverability or have the customer message the account
first. A bounded dialog scan alone is never proof that a phone is unregistered.
An operator can retry the existing red message after successful resolution;
deployment itself must not resend it.
