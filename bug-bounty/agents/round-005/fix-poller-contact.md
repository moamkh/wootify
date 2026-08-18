# Round 005 — Fix agent: bale_polling_service contact payload + shutdown hygiene

**File edited (only):** `app/services/bale_polling_service.py`
**Date:** 2026-08-10

## Bug 1 (CRITICAL) — AttributeError on every contact-share message

- **Symptom:** `_platform_update_to_event` (now line 990) called
  `self._extract_contact_payload(message)`, but the method did not exist on
  `BalePollingService` (only `_extract_contact_text`). Every contact-share
  update on bale/telegram platforms raised `AttributeError`, was retried, then
  dead-lettered — the feature was totally broken.
- **Fix:** implemented `_extract_contact_payload` as a `@staticmethod` on
  `BalePollingService`, placed directly after `_extract_contact_text` (now
  ~line 1117). Semantics mirror `adapters/bale_pv.py::_extract_contact_payload`
  (~:426) adapted to the Bot-API message shape used here: reads
  `message['contact']` and returns a normalized dict
  `{'phone_number', 'first_name', 'last_name', 'user_id'}` or `None` when the
  contact is absent or has no phone number.
- **Contract check:** the caller stores the return verbatim as
  `event['contact']` (`'contact': contact_payload`), so `None`-when-absent and
  the four-key dict match exactly.

## Bug 2 (HIGH) — tasks cancelled but never awaited on shutdown/disable

- **Symptom:** `stop()` and the manager's instance-disable path in
  `_run_manager` called `task.cancel()` without ever awaiting the tasks,
  producing "Task was destroyed but it is pending" and mid-write kills.
- **Fix in `stop()` (~:86):** snapshot poll tasks and non-done SMS-sync tasks
  before clearing the dicts; cancel all; cancel + await the manager task first
  (so the manager cannot respawn pollers while we drain); then
  `await asyncio.gather(*pending_tasks, return_exceptions=True)` guarded by an
  empty-list check. Still safe to call once; `connector_registry.close_all()`
  ordering preserved.
- **Fix in `_run_manager` disable path (~:117):** collect the cancelled poll
  task and SMS-sync task per disabled key into `disabled_tasks`, then
  `await asyncio.gather(*disabled_tasks, return_exceptions=True)` (guarded)
  after the loop.

## Verification

- `C:\Users\amin\Desktop\wootify_instance_manager\.venv\Scripts\python.exe -m py_compile app/services/bale_polling_service.py` → exit code 0, no output.
- Grep confirms exactly one definition of `_extract_contact_payload` (line 1119) and one caller (line 990).
- Runtime behavior (contact-share delivery, clean shutdown logs) not exercised — no tests run beyond compile.

## Style / scope

- Single-quote style of the polling-service file matched (the bale_pv adapter uses double quotes; not copied verbatim).
- No other files touched.
