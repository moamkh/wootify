# Reopen resolved conversations per inbox

## Operator controls

Open an existing instance → Configuration → Chatwoot → **Conversation behavior**.
Toggle **Reopen resolved conversation** for the relevant inbox and save the
instance. No inbox recreation, account login, application restart, or database
migration is required. It can be switched on and off at any time.

- Off (the default for new/unconfigured instances): the next new customer
  message after resolution starts a new conversation.
- On: the next new customer message reopens the existing resolved conversation
  in that inbox. An already open conversation is reused instead.
- Saving the setting does not itself reopen any conversation. It governs future
  delivery attempts, including queued messages that have not been delivered yet.
- Enterprise Bale and Telegram routes have separate inbox controls. Link each
  route inbox first. Changing one does not change the other.
- Previously saved legacy `reopen_conversation` values are respected when there
  is no explicit override; existing instance settings are not bulk rewritten.

This applies to Wootify-managed Bale bot, Telegram bot, Bale PV, Eitaa PV,
Instagram PV, and enterprise Bale/Telegram route delivery. WhatsApp inboxes
managed directly by Evolution are outside Wootify's delivery path and are not
affected by this feature.

## Persistence and API

The existing encrypted instance Chatwoot configuration stores the settings;
there are no new database columns or migrations. Overrides use Chatwoot inbox
IDs and are scoped to the connector instance and its Chatwoot account:

```json
{
  "chatwoot": {
    "reopen_conversation_by_inbox": { "57": true, "58": false }
  }
}
```

Send this to `PATCH /api/v1/instances/{instance_key}` for existing instances.
Partial patches merge the override map, preserving settings for other inboxes,
credentials, tokens, and platform sessions. Values must be booleans and keys
positive inbox IDs. The old `chatwoot.reopen_conversation` remains a fallback.

## Implementation and safeguards

- `conversation_policy.py` centralizes override precedence and selection.
  Remote conversation selection is restricted to the current contact/inbox,
  prefers unresolved tickets, and otherwise chooses the newest resolved one.
- The adapter bridge previously always skipped resolved conversations and
  ignored its editor setting. It now reads live configuration and verifies
  remote status, including when a resolution webhook was missed.
- Reopening explicitly requests Chatwoot status `open`. A failed reopen request
  propagates to delivery retry handling; no new message is posted as a success
  after that failure.
- A new ticket gets a separate local conversation mapping. Old messages retain
  their original mappings for reply/delete/edit handling. Already delivered
  duplicate events and edits use their original ticket rather than creating or
  reopening another ticket.
- Enterprise flows recover resolved sessions on the same route and reopen only
  existing remote conversations. Missing conversations are recreated, not
  reopened. A route's old inbox is never reopened after the route is relinked.
- The UI exposes the setting when editing, not only creating; standard Telegram
  also preserves its inbox fields and conversation settings in update payloads.
- No Chatwoot contact identifiers (including WhatsApp identifiers) are changed.

## Verification

Regression tests cover the existing-instance PATCH API, repeated toggles,
partial-map preservation and validation, all Wootify delivery paths, missed
resolution webhooks, inbox isolation, reopen failures, deleted remote tickets,
historical mappings, and delayed duplicate events. Frontend checks cover payloads
for every platform, independent route settings, and editable rendering.

Local verification: 479 backend/protocol tests passed; frontend conversation
settings tests, authentication tests, and the production build passed.

## Demo deployment

Deployed to demo `172.21.1.69` on October 14, 2026 (Tehran), retaining version
**7.1.1**. Artifact ID: `f695a646d070a9a8`. The limited source package includes
the previously committed Eitaa attachment-reply hotfix `a17469d` alongside this
feature; existing Eitaa session-renewal code was preserved.

- Test UI: `http://172.21.1.69:5173/instance-manager/`.
- Frontend was built with backend `http://172.21.1.69:8000`, not localhost.
- Backup: `/opt/wootify-backups/demo-reopen-20261013T232218Z` (UTC folder name).
  Contains original changed source files, both served frontend copies, config,
  sessions, and a verified PostgreSQL dump.
- Before/after counts: 4 instances, 19 conversations, 121 message mappings,
  1 contact mapping. Instance configuration digest and `.env` unchanged.
- Database revision remains `f7a0c02d21b3`; no migrations were run.
- API health/version, module imports, and both served frontend indexes passed.
- No customer messages were sent or replayed, and no reopen setting was enabled
  automatically. Operators can enable it on selected demo inboxes for testing.
- Production was not changed; no version bump, Git push, or tag creation was
  performed for this deployment.
