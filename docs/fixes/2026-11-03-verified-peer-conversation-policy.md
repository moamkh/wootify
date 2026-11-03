# Apply conversation policy to a native peer, including verified aliases

The split in Bale conversations 3775/3782 was not a disabled reopen toggle. The
toggle was already enabled, but canonical contact6076 and verified alias5856
were selected independently. Late outgoing webhooks could also switch the
active pointer between their tickets.

`ChatwootBridgeService._get_or_create_conversation` now considers conversations
across the canonical contact and verified aliases in the same instance/account
scope. The selected ticket retains its actual owning contact. Historical rows
and message IDs are retained. `ConversationMappingService.upsert(promote=False)`
records an outbound route without replacing an existing active route for that
peer. Authoritative inbound selection still promotes its selected ticket.

The new authenticated `/webhooks/chatwoot/{instance}/conversation-policy`
preflight lets Chatvand resolve identity before creating a ticket. It validates
the configured account/inbox and the existing API-channel HMAC token fetched
from that configured Chatwoot. It never trusts caller-supplied base URLs or
phones. An unmapped contact is fetched from Chatwoot and its actual phone is
independently resolved by the active Bale/Eitaa adapter before saving a local
canonical mapping or verified alias. A known mapping works while the adapter is
offline. Ambiguous/unverifiable recipients fail closed and can be retried.

The response contains scoped canonical/verified contact IDs and the current
per-inbox reopen policy; it creates neither a ticket nor a platform message.
Chatvand serializes selection by native peer and applies the live toggle to all
aliases. WhatsApp contact identifiers are never written or replaced.

Regression coverage includes fresh phone verification, foreign WhatsApp
identifier preservation, invalid auth, cross-inbox rejection, both reopen states,
alias-aware inbound reuse and historical outbound pointer stability. No schema,
version or tag change is required. Source remains uncommitted as requested.

For an already split history, `verified_conversation_redirects` records an
audited old ticket -> target ticket, native peer and inbox in Wootify's encrypted
instance configuration. Normalization preserves and validates this map during
later settings edits. Every delayed callback rechecks both local conversation
rows, their common native peer and inbox before translating its conversation
ID; its message ID and content are unchanged. This protects confirmed receipts
against replay after mappings move with a repaired history. Outbound upserts
also preserve an existing ticket's actual owning contact. The preflight returns
the active mapped ticket as a preference, so creation and ingestion choose the
same open ticket when historical aliases already exist.
