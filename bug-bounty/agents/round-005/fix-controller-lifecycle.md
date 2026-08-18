# Round 005 — Fix: Controller lifecycle & unbounded queries

Date: 2026-08-10
Scope: app/controllers/api_v1_controller.py, app/repositories/conversation_repository.py, app/repositories/message_mapping_repository.py (only these 3 files edited)

## Bug 1 (HIGH) — Ghost runtime connections

- `delete_instance` removed the DB row but left the BalePvAdapter WebSocket live in
  `runtime_registry` (`disconnect_instance` existed with zero call sites).
- Fix: `delete_instance` is now `async def` and calls
  `await runtime_registry.disconnect_instance(instance_key)` before the DB delete
  (adapter errors are already swallowed/logged inside `disconnect_instance`).
- `patch_instance`: when `payload.is_enabled is False`, the runtime is now disconnected
  the same way after the update succeeds. Re-enable needs no explicit reconnect: the poll
  manager (`app/services/bale_polling_service.py` `_run_instance`, ~line 200) calls
  `runtime_registry.connect_instance(...)` every poll iteration for enabled
  `bale_pv_enterprise` instances, and `connect_instance` re-creates the adapter when the
  registry entry is gone (verified: early-return only when an entry exists with
  `status == "open"`).

## Bug 2 (MED) — Silent degrade in bale_pv_validate_code

- Bare `except Exception: pass` around `runtime_registry.connect_instance` reported
  `authenticated` while the runtime was dead.
- Fix: exception is now logged via `logger.warning` and the response `detail` gains a
  `warning=runtime_connect_failed` suffix. Auth itself still succeeds (jwt was saved).

## Bug 3 (HIGH) — Unbounded queries

- `ConversationRepository.list_by_instance(...)` gained keyword-only
  `limit=None, offset=None, include_messages=False` params; LIMIT/OFFSET pushed into SQL.
  The eager `selectinload(Conversation.message_mappings)` is only applied when
  `include_messages=True`; `runtime_state` selectinload retained (cheap 1:1).
- `MessageMappingRepository.list_by_conversation(...)` gained keyword-only
  `limit=None, offset=None`; LIMIT/OFFSET pushed into SQL. Defaults keep existing callers
  (services) backward compatible.
- `GET /instances/{key}/conversations`: new query params `limit` (default 50, max 200),
  `offset` (default 0), `include` (`?include=messages` restores eager message_mappings).
  Calls the repository directly (services were out of edit scope). `q` filter still applied
  in-memory on the returned page. Response schema unchanged (`items` only — list schemas
  do not allow extras, so no paging-hint field was added).
- `GET /instances/{key}/conversations/{id}/messages`: new query params `limit`
  (default 50, max 200), `offset` (default 0); calls the repository directly.

## Verification

- `python -m py_compile` on all 3 touched files with the project venv interpreter:
  exit code 0, no output (all compile clean).

## Notes / remaining risk

- Existing clients of the two list endpoints now receive at most 50 items by default
  (previously unbounded). This is the intended fix but is an observable behavior change.
- Behavior change only verified by compilation; no runtime test suite was run.
