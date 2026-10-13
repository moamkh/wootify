# Changelog

## 7.1.2 — 2026-10-14

- Add an editable per-inbox **Reopen resolved conversation** setting. Apply
  saved changes to the next incoming message without recreating the inbox,
  restarting the connector, or logging in again. Default to creating a new
  conversation; respect existing saved legacy settings.
- Honor the policy in Bale PV, Eitaa PV, Instagram PV, standard Bale/Telegram
  bot bridges, and separate enterprise Bale/Telegram route inboxes. Fix the
  adapter path that previously ignored the reopen setting, and the Telegram
  editor payload that discarded inbox fields/settings.
- Preserve historical conversation/message mappings when starting a new ticket,
  instead of moving old messages to the replacement. Delayed duplicate events
  and edits target the original ticket and cannot reopen/create another one.
- Verify remote status even if a resolution webhook was missed. Reopen explicitly
  as `open`; retain delivery retry behavior when that request fails. Distinguish
  missing enterprise tickets from resolved tickets and protect relinked routes.
- Include the committed 7.1.1 Eitaa hotfixes: renew expired authentication tokens
  when possible without a new phone login, and map replies to any file/image in
  an outgoing attachment batch back to its original Chatwoot message.
- Preserve Chatwoot/WhatsApp contact identifiers, credentials, sessions, and
  inbox configurations. No schema migration or dependency change is introduced.
- Verify 479 backend/protocol tests, frontend conversation-setting checks,
  authentication/API-origin checks, and per-server frontend builds.

## 7.1.1 — 2026-10-11

- Correct Bale PV forwarded-message parsing so forwarded files are delivered
  to Chatwoot rather than discarded as empty service notices. Include a
  Forwarded label and source when available, without confusing forwards with replies.
- Extend Eitaa outbound routing with exact-phone verification and instance-owned
  mappings. Reuse contacts across instances by normalized phone, repair verified
  phone-less duplicates through local aliases, and preserve WhatsApp identifiers
  and meaningful existing contact names. Ambiguous identity matches fail closed.
- Replace Eitaa's limited dialog-history scanning with updates.getDifference.
  Atomically persist events and their cursor in the existing database queue,
  acknowledge only after commit, and retry delivery with per-peer ordering.
  Detect expired sessions and update gaps instead of silently losing messages.
- Follow Eitaa Web's server-provided foreground polling interval and show
  automatic polling in the instance form.
- Send Eitaa attachments individually before the separate caption/text message.
  Checkpoint each acknowledged part and use stable protocol random IDs so
  retries resume remaining parts instead of resending the whole attachment batch.
- Reject unacknowledged sends, expose failures through Chatwoot's failed/red
  message status and private notes, and restore sent status after a successful retry.
- Normalize nested Eitaa reply headers and scope parent/outbound mapping lookups
  to the correct instance, platform peer, and Chatwoot conversation.
- Apply explicit Eitaa deletion events to the original Chatwoot message, with
  ordering protection and fail-closed resolution of ambiguous global deletions.
- Render Eitaa locations/venues as Google Maps coordinate links and contact
  cards as text instead of attempting invalid attachment downloads.
- Add an optional read-only Eitaa Connector status conversation, configured in
  the instance UI, reporting polling/authentication failures and blocking replies.
- Include the previously committed frontend safeguard against remote builds
  using a localhost backend URL. Add focused regression coverage for these fixes.
- No database schema migration or dependency upgrade is introduced. Existing
  history is not automatically backfilled, and channel-gap recovery still requires
  a dedicated recovery path. See docs/releases/v7.1.1.md for release details.

## 7.1.0 — 2026-10-04

- Persist every Bale PV WebSocket update in the existing inbound-event queue
  before attempting delivery to Chatwoot. A database write failure retains the
  drained batch in memory and pauses further polling until it can be committed.
- Wake the queue worker as soon as a batch is persisted, and retry failed
  deliveries with backoff across process restarts and Chatwoot outages. A
  blocked conversation no longer prevents updates for other Bale peers from
  being delivered.
- Deduplicate repeated Bale updates while keeping message edits and deletions
  distinct. Treat explicit bridge rejections and missing adapter runtimes as
  failures to retry instead of silently dropping those updates.
- Add regression tests for persistence-before-delivery, deduplication,
  per-peer ordering, failed delivery, and poller routing. This release reuses
  the existing `inbound_event_retries` table; no schema migration is added.

## 7.0.11 — 2026-10-01

- Preserve Bale PV quoted replies in both directions. Parse the field-7 reply
  wrapper separately from forwarded-message metadata, then resolve its Bale
  parent RID to Chatwoot's `in_reply_to` message ID.
- Encode outbound Bale quotes in `SendMessage` field 5 as a quoted-message
  reference containing the conversation peer and parent RID. Field 4 is
  `isOnlyForUser`, so the previous encoding sent reply text without its quote.
- Log reply parent IDs at mapping and send boundaries for diagnosis. Add
  regression tests for the observed reply frame and outbound wire format.
  No database migration is required.

## 7.0.10 — 2026-09-30

- Bridge Bale PV location shares as clickable Google Maps links in Chatwoot.
  Decode the field-7 location payload and validate coordinates so these
  messages are no longer misclassified as service notices and dropped.
- Add parser and connector regression tests using the observed Bale location
  payload shape. No database schema changes.

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
