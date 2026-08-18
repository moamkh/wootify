# Round 004 — fix-bridges

Scope: `app/services/bridge_service.py`, `app/services/chatwoot_bridge_service.py` only.

## Changes

### 1. HIGH — Dead group-sender-contact feature (chatwoot_bridge_service.py)
`avatar_bytes=sender_avatar_bytes if sender_created else None` referenced
`sender_created` in its own RHS -> `UnboundLocalError` on every group/channel
message, swallowed by the broad `except`. Fix: initialize `sender_created = False`
before the call and pass `avatar_bytes=sender_avatar_bytes` unconditionally —
`_get_or_create_contact` only uploads the avatar on its create path, so the
avatar is attached solely for newly created sender contacts (the evident
intent). Existing try/except resilience kept.

### 2. HIGH — Stale contact mapping after remote contact deletion (chatwoot_bridge_service.py)
`_recreate_chatwoot_conversation` reused the deleted `contact_id`; the
`create_conversation` 404 propagated uncaught, permanently breaking inbound for
that chat. Fix: wrapped the create in `try/except httpx.HTTPStatusError`; on a
404 the stale `chatwoot_contact_id`/`chatwoot_conversation_id` fields on the
Conversation row are cleared (committed), then the full remote
get-or-create-contact + create-conversation flow is re-run exactly once
(bounded). New optional kwargs `from_name`, `phone_number`, `chat_type`,
`platform_key` (defaults preserve old signature); the single call site passes
the event values. Clear `recreate_contact_missing` warning logged.

### 3. MED — Phone-resolution cache check-then-insert race (both files)
Concurrent inserts under `uq_bale_pv_resolved_phone` raised unhandled
`IntegrityError`. Fix in both `_resolve_bale_pv_phone` implementations: wrap
`db.add()` + `db.commit()` in `try/except IntegrityError` -> `db.rollback()` ->
re-query the row and return it (re-raise if still missing). Added
`from sqlalchemy.exc import IntegrityError` to both files. Also made
`bridge_service.py`'s cache-hit path re-populate
`runtime.adapter.cache_access_hash` (via `app.runtime_registry.get_runtime`,
lazy import guarded by try/except) mirroring chatwoot_bridge_service.py so the
services don't diverge after an adapter restart.

### 4. MED — Outbound platform_message_id always None for bale_pv (bridge_service.py)
Code did `(platform_response or {}).get('id')`, but the Bale PV connector
returns `{'ok': True, 'result': {...}}` envelopes. Investigation of actual
shapes:
- `bale_pv_connector.send_text` -> `{"ok": True, "result": {"raw_response": <hex|None>}}`
  (send is fire-and-forget via `send_update`; response is always None — no rid/message_id exists).
- `bale_pv_connector.send_media` -> `{"ok": True, "result": {"file_id": ..., "name": ...}}`
  (`file_id` is a media file id, not a message id — deliberately not used).
- Bot `bale_connector.send_text/send_media` -> flat `{"id": str(message_id)|None, "raw": ...}`.

Fix: new `_extract_platform_message_id` static helper checks top-level `id`
(bot shape), then `result.message_id` / `result.rid` / `result.id` /
`result.date` inside the envelope, falling back to `None` gracefully. For
current PV builds the honest result remains None (no message id is returned by
the wire protocol ack); the helper future-proofs envelope shapes.

### 5. LOW — Hardening (chatwoot_bridge_service.py)
- Guarded both `int(conversation.chatwoot_conversation_id)` conversions
  (initial get-or-create path and post-recreate path) with
  `try/except (TypeError, ValueError)` that logs a warning and falls back to 0,
  which drives the existing 404 -> recreate fallback instead of crashing.
- Contact fast-path query in `_get_or_create_contact` now filters
  `Conversation.is_active.is_(True)` and orders by `Conversation.id.desc()`
  for deterministic selection when duplicate rows exist.

## Verification
- `python -m py_compile app/services/bridge_service.py app/services/chatwoot_bridge_service.py`
  with `.venv\Scripts\python.exe` -> exit 0, no output (both compile).

## Notes / risks
- Concurrent subagents edited `app/connectors/bale_pv_connector.py` and
  `bale_connector.py` during this round; re-verified send return shapes after
  their changes — unchanged at check time (see #4).
- The bug-2 fallback relies on Chatwoot returning HTTP 404 for
  create_conversation with a deleted contact; non-404 errors still propagate
  as before.
