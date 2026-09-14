# Changelog

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
