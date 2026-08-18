# Round 4 — Fix: bale_polling_service message-loss / corruption bugs

**File edited (only):** `app/services/bale_polling_service.py`
**Verify:** `.venv\Scripts\python.exe -m py_compile app/services/bale_polling_service.py` → exit 0 (no output).

## Changes

1. **CRITICAL — `_extract_contact_text` reconstructed** (was ~:1031-1062).
   - Removed inverted `if full_name: return None` that dropped contact text whenever a name existed.
   - Deleted the entire orphaned content-sniffing block referencing undefined name `content` (PNG/JPEG/GIF/WEBP/OggS/WAVE/ID3/ftyp magic checks) — a guaranteed `NameError` poison-message that wedged the instance's inbound queue.
   - New behavior: builds `Shared contact: <full_name> <phone>` from `first_name`/`last_name`/`phone_number`; returns `None` only when name and phone are both empty.

2. **CRITICAL — batch-end offset persist no longer defeats failure handling.**
   - Removed `max_update` (bumped for every update). Now tracks `max_processed_update`, advanced only in the `update_processed` success block.
   - Batch-end `_update_runtime_state_with_retry` and `_remember_last_update_id` persist `max_processed_update`, so failed updates are refetched by the next poll.

3. **HIGH — bounded retry with dead-letter instead of silent loss.**
   - New state: `self._update_fail_counts: dict[str, dict[str, int]]` (per-instance, per-update-id attempt counters) and class constant `_MAX_UPDATE_ATTEMPTS = 3`.
   - New helpers: `_record_update_failure(instance_key, update_id) -> bool` (increments counter; at 3 attempts logs ERROR `update_dead_letter instance=… update_id=… attempts=…` and returns True = drop; updates without a numeric id are dropped immediately since the offset cannot track them) and `_reset_update_failure(...)` (pops counter on success).
   - Bridge path: normalize (`_normalize_with_adapter` / `_platform_update_to_event`) + ingest are now inside a single try; on exception `update_processed` is NOT set — the counter decides. Removed the old "mark processed even on bridge failure" line.
   - Same counter applied to both enterprise failure paths (`bale_enterprise`, `telegram_enterprise`) so fix 2 cannot poison-loop.
   - Counter reset in the per-update success block; per-instance counters popped in manager cleanup and cleared in `stop()`.

4. **MEDIA — empty attachment download raises.**
   - `_platform_update_to_event`: `if not content: raise RuntimeError('attachment download returned empty content file_id=…')` when a `file_id` was present. The raise is caught by the bridge-path try (edit 3) and fed to the retry counter — no media message is delivered stripped of its media. Former `if content:` guard block dedented.

5. **MED — `poll_interval` honored with bounded latency.**
   - Idle wait: `await asyncio.wait_for(self._stop.wait(), timeout=min(float(poll_interval), 2.0))` (was hardcoded `timeout=1`).

6. **LOW — `_share_phone_prompted` pruned on instance cleanup.**
   - Manager's disabled-instance cleanup loop now rebuilds the set without entries whose `instance_key` matches the removed key (entries are `(instance_key, chat_id)` tuples).

## Known residual gap (per audit spec, accepted)

`max_processed_update` advances over any succeeded update, so if update N fails
but N+1 in the same batch succeeds, the persisted offset passes N and its retry
counter never fires. The counter protects the common case (failure at the batch
tail / transient Chatwoot errors on newest updates). Closing this fully would
require stopping offset advancement at the first failed update per batch, which
was outside the prescribed fix.

## Verification

- `py_compile` exit code 0, empty stderr (this session).
- `grep max_update` → no matches (no stale references).
- Not run: unit/integration tests (none targeted in scope).
