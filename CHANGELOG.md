# Changelog

## 7.0.9 — 2026-09-30

- Recover Bale PV delivery when Chatwoot has multiple contacts for the same
  verified Bale user. Keep the original contact mapping and record additional
  contacts as scoped, verified aliases after a fresh Bale phone lookup or a
  matching legacy Wootify identifier.
- Route later messages from verified aliases without repeating phone lookup.
  Prevent a Chatwoot contact from being claimed by a different platform peer;
  fail closed when identity cannot be verified. Preserve Chatwoot identifiers.
- Add a `contact_aliases` migration and regression tests for duplicate contacts,
  mismatched phone resolution, and cross-peer claims.

## 7.0.8 — 2026-09-30

- Preserve contact identifiers owned by WhatsApp and other integrations at the
  Chatwoot HTTP client boundary. Wootify contact routing uses instance-owned
  mappings, and Bale, Eitaa, Telegram, and enterprise contacts share durable
  contact resolution.
- Recover contact creation after lost HTTP responses or local database failures
  through a saved API-inbox source key; reject ambiguous matches and stop
  delivery when account scope or peer identity conflicts.
- Add the `contact_creations` migration, targeted failure/concurrency tests,
  and deployment notes in `docs/contact-identity-hardening.md`.

## 7.0.7 — 2026-09-30

- Store Wootify-managed platform contact identities in an instance-scoped
  `contact_mappings` table instead of writing them into Chatwoot contact
  identifiers. Preserve identifiers owned by WhatsApp or other connectors;
  import older Wootify identifiers into the local map without modifying them.
- Reuse existing Chatwoot contacts by exact phone match and keep Bale peer
  type in Wootify's map for group/channel routing. Add stale-contact recovery
  and a database migration for the new mapping table.
- Thread Bale PV replies in Chatwoot by translating the quoted Bale message ID
  through the stored message mapping into Chatwoot's `in_reply_to` field.
  Messages still arrive normally if their quoted parent is unavailable.
- Preserve the target of Chatwoot operator replies sent to Bale PV.

## 7.0.6 — 2026-09-26

- Send Bale Enterprise content and its restored root keyboard in one message
  for manuals, catalogs, and addresses. Combine welcome and phone validation
  text with their respective menu or prompt instead of sending a second bubble.
- Add regression coverage for the single-response address and phone flows.
  No database schema changes.

## 7.0.5 — 2026-09-21

- Prevent Chatwoot voice recordings from enabling Send until the asynchronous
  upload and attachment registration have both completed.
- Propagate direct and indirect upload completion back to the reply composer so
  failed or unfinished recordings cannot create attachment-free messages.
- Reject outgoing Chatwoot webhooks containing neither text nor attachments,
  mark the Chatwoot message as failed, and add the existing delivery-failure
  note instead of sending an empty message to Bale.
- Add regression coverage proving empty payloads never call the platform text
  or media send APIs. No database schema changes.

## 7.0.4 — 2026-09-20

- Fixed Web Bale media batches so every photo or file in an album is delivered to Chatwoot as its own message.

## 7.0.3 — 2026-09-16

- Prevent an outbound conversation-mapping lock from being held across Bale
  read-receipt, response-delay and message-send awaits.
- Run PostgreSQL mapping writes in short worker-thread transactions and apply
  bounded lock and idle-transaction timeouts so one row conflict cannot freeze
  the API event loop.
- Persist accepted Chatwoot callbacks before acknowledging them and recover
  pending deliveries after a worker restart, with bounded retry backoff.
- Roll back failed background delivery sessions before performing any further
  network work.
- Reduce account-wide Chatwoot webhook subscriptions to the three events the
  bridge consumes, eliminating contact, inbox, typing and conversation noise.
- Add the `chatwoot_webhook_deliveries` reliability migration and regression
  coverage for committed mapping boundaries and durable callback re-arming.

## 7.0.2 — 2026-09-16

- Route outbound Chatwoot messages through instance-owned conversation
  mappings and safely parse Bale, Eitaa, Telegram and Instagram identifiers.
- Type Bale contacts as users, groups or channels and record confirmed
  sendability without misclassifying protocol peer types.
- Mark failed Chatwoot deliveries with the native failed state and external
  error so agents see the red undelivered indicator and Retry action.
- Use Bale's real `MessageRead` RPC with peer-aware read and typing state.
- Decode Bale's `deletedMessage` WebSocket marker and propagate native Bale
  deletions to Chatwoot without creating a delete echo loop.
- No database schema changes.

## 7.0.1 — 2026-09-13

- Reconnect reliability follow-up: fail disconnected Bale RPCs as connection
  errors instead of cancelling their callers; restart unexpectedly finished
  polling tasks and report stale Bale PV polling as unhealthy.

- Resolve the recipient, rather than the authenticated sender, for first
  outgoing Bale PV private messages when the recipient is not cached.
- Prefer saved Bale contact aliases, then public profile names and usernames.
- Repair existing generic Bale private-contact names when a real name becomes
  available, preserving contact IDs, history and agent-customized names.
- No database schema changes or bulk contact backfill.

## 7.0.0 — 2026-09-12

### Eitaa PV

- Added a phone-authenticated Eitaa personal-account connector, including the
  instance-manager platform selection, Chatwoot inbox setup, persistent session
  storage, and Eitaa Web-compatible polling.
- Added inbound and outbound text, replies, photos, documents, video and audio.
  Upload finalization remains on the selected Eitaa upload host, which fixes
  `INTERNAL_SERVER_ERROR10` after successful file-part uploads.
- Added Eitaa contact identity synchronization, read acknowledgements, online
  presence while polling, typing indicators, attachment MIME preservation, and
  outgoing-echo de-duplication.

### Bale PV and bridge reliability

- Aligned Bale PV’s poll cadence, online state, read cache and typing behavior
  with the Eitaa workflow.
- Improved Chatwoot attachment downloads, media mappings, inbox ownership
  checks and duplicate-delivery prevention.

## 6.0.4 — 2026-09-09

### Bale PV hotfix

- Prevented Bale first-party security notices (login-code and connected-device
  alerts) from being created as customer messages in Chatwoot.
- Required an exact platform contact identifier after Chatwoot's fuzzy contact
  search, preventing short Bale system IDs from being attached to unrelated
  customer contacts and triggering inbox automations.
- Added regression coverage for system notices, normal customer text, and
  exact-identifier contact resolution.

## 6.0.3 — 2026-09-09

### Instagram PV

- Added an Instagram personal-DM connector with encrypted session-cookie or
  credential authentication, proxy support, challenge handling, independent
  polling and retry-safe delivery.
- Added normalized inbound and outbound text, replies, photos, MP4 H.264/AAC
  video, and M4A/AAC voice messages. Unsupported documents, GIFs and audio
  formats fail before any caption is sent.
- Reduced private-API pacing restored from old session files and bounded inbox
  reads to improve response latency without blocking other account pollers.
- Added durable message mappings for outgoing messages, media captions and
  delete/unsend operations. Duplicate Chatwoot callbacks and Instagram
  outgoing echoes are ignored.
- Fixed media echoes being incorrectly represented as customer edits in
  Chatwoot. Instagram deletes now unsend once, while editing remains explicitly
  unsupported by the installed Direct API.

### Reliability

- Made delivery failure notes platform-specific and deduplicated.
- Added a bounded Instagram media-upload acknowledgement timeout.
- Added regression coverage for media validation, session pacing, echo handling,
  duplicate deletes, proxy propagation and polling isolation.
