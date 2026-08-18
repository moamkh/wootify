# fix-webhook-bg — round-004 change log

Date: 2026-08-10
Scope: harden the ack-first Chatwoot webhook background delivery (uncommitted change returned HTTP 200 immediately and delivered via `asyncio.create_task`). Files edited (only these two):
- `app/controllers/api_v1_controller.py`
- `app/main.py`

## Fix 1 (HIGH) — TOCTOU duplicate window / lost ordering: per-(instance, conversation) delivery locks

**File:** `app/controllers/api_v1_controller.py` (new module-level helpers near `_webhook_delivery_tasks`)

**Before:** Chatwoot retries/redeliveries of the same message ran as concurrent background tasks with separate DB sessions; both could pass the downstream dedup SELECT before either committed -> double outbound sends. Ordering within a conversation (delete before create, reply before parent mapping) was also lost.

**After:**
- `_chatwoot_delivery_lock_key(instance_key, payload)` derives a serialization key: `conversation.id` -> fallback `conversation_id` -> fallback message `id` -> fallback `instance_key`.
- `_webhook_delivery_locks: dict[str, asyncio.Lock]` (module-level) with `_get_webhook_delivery_lock()` (creates on demand, FIFO eviction at `_WEBHOOK_DELIVERY_LOCKS_MAX = 1000` entries) and `_release_webhook_delivery_lock()` (pops the entry once unlocked and identity-matched — simple cleanup).
- New `_deliver_chatwoot_webhook_guarded()` holds the keyed lock around the whole background delivery; `_handle_chatwoot_webhook` now schedules the guarded wrapper instead of the raw delivery coroutine.

## Fix 2 (HIGH) — Silent loss of returned failure dicts

**File:** `app/controllers/api_v1_controller.py` (`_log_delivery_result` + `_deliver_chatwoot_webhook_background`)

**Before:** the background wrapper only logged raised exceptions; services often **return** failure dicts (`{'ok': False, ...}`, `status: 'failed'`, `message: 'delivery_failed'`) which were silently dropped after the ack-first change.

**After:** the dispatch result is captured and passed to `_log_delivery_result()`, which logs a WARNING with `instance_key`, `route_key`, `status`, and `detail`/`message` when the dict indicates not-ok (`ok is False`, `status in ('failed', 'error')`, or `message == 'delivery_failed'`). Successes stay quiet; `status == 'duplicate'` logs at DEBUG. Non-dict results are ignored.

## Fix 3 (HIGH) — Unbounded fan-out: semaphore + delivery timeout

**File:** `app/controllers/api_v1_controller.py`

- Module-level `_webhook_delivery_semaphore = asyncio.Semaphore(20)` caps concurrent background deliveries.
- Delivery is wrapped in `asyncio.wait_for(..., timeout=_WEBHOOK_DELIVERY_TIMEOUT_SECONDS)` (300s). `asyncio.TimeoutError` is caught in `_deliver_chatwoot_webhook_guarded` and logged as a WARNING delivery failure (`error=delivery_timeout`); `wait_for` cancels the hung inner coroutine. (Caught in the guarded wrapper, not the inner one, because `wait_for` raises the timeout at the await site outside the inner coroutine.)

## Fix 4 (MED) — Shutdown drain of in-flight deliveries

**File:** `app/main.py` (lifespan shutdown, placed BEFORE `polling_service.stop()` so the polling service is still up while deliveries finish)

- Imports `_webhook_delivery_tasks` from the controller and adds `import asyncio`.
- On shutdown: snapshots non-done tasks, `await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=5)`; on timeout cancels the remainder, gathers again with `return_exceptions=True`, and logs how many were cancelled (`shutdown: cancelled %d in-flight webhook deliveries`). A `shutdown: draining %d in-flight webhook deliveries` INFO line is emitted when any are pending.

## Fix 5 (LOW) — Dead `except RuntimeError` branch comment

**File:** `app/controllers/api_v1_controller.py` (`_handle_chatwoot_webhook`)

Branch kept (response contract unchanged) but the comment now states it only covers RuntimeErrors raised during instance/payload resolution, before the background task is scheduled — delivery failures can no longer propagate there.

## Verification

- `C:\Users\amin\Desktop\wootify_instance_manager\.venv\Scripts\python.exe -m py_compile app/controllers/api_v1_controller.py app/main.py` — exit code 0, no output (both files compile).
- No runtime/tests executed in this round; behavior verified by code review only.
