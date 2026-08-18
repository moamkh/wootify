# fix-bot-bridge — round-002 change log

Date: 2026-08-09
Scope: performance fixes in outbound/inbound media paths. Files edited (only these two):
- `app/services/bridge_service.py`
- `app/adapters/bale_pv.py`

## Fix 1 — Parallel outbound media send (Chatwoot -> Bale)

**File:** `app/services/bridge_service.py` (~line 411, outbound webhook message path)

**Before:** attachments were sent in a serial `for index, attachment in enumerate(attachments): ... await connector.send_media(...)` loop; each iteration did a Chatwoot download + Bale upload before starting the next.

**After:** the loop body was extracted into a local async `_send_attachment()` helper guarded by `asyncio.Semaphore(3)`, and all attachments are dispatched with `asyncio.gather(..., return_exceptions=True)`.

**Semantics preserved:**
- Per-attachment error tolerance: a failing attachment no longer cancels the others. Failures are logged via `logger.warning('outbound_media_attachment_failed instance=%s conversation_id=%s index=%s error=%s', ...)` (matches existing log style).
- `platform_response` is still taken from attachment index 0 when it succeeds; if index 0 failed but others succeeded, the first successful result is used instead.
- If **every** attachment fails, the first exception is re-raised so the existing outer `except` block marks the message `MessageStatus.failed` and returns `delivery_failed` — identical to the previous whole-message failure path.
- `caption`, `quoted`, `access_hash`, filename normalization, and relative-URL resolution (`data_url`/`content` prefixed with Chatwoot `base_url`) unchanged.

## Fix 2 — Offload CPU-bound Pillow transcode off the event loop

**File:** `app/adapters/bale_pv.py` (`resolve_attachments`, ~line 345)

**Before:** `converted, ext, converted_ct = self._convert_webp(content)` ran the PIL WEBP->JPEG/PNG transcode synchronously on the event loop for every inbound WEBP sticker.

**After:** `converted, ext, converted_ct = await asyncio.to_thread(self._convert_webp, content)`. The JPEG-first-then-PNG-fallback logic inside `_convert_webp` is untouched (still doubles CPU only on JPEG failure, as before — just offloaded). The sync helpers `_convert_webp_to_jpeg` / `_convert_webp_to_png` / `_convert_webp` are unchanged, so no other call sites are affected. `asyncio` was already imported in this module; no new dependencies.

## Fix 3 — Inline `asyncio.sleep` pacing audit

All `asyncio.sleep` occurrences in `app/services/bridge_service.py`:
- L1311 (2.0s) — bulk contact sync loop (`sync_bale_pv_contacts`)
- L1462 (0.5s) — bulk contact removal loop (`remove_bale_pv_contacts`)
- L1588 (0.2s) — historical message import inside dialog sync
- L1603 (1.0s) — per-dialog pacing in dialog sync endpoint
- L2029 (0.8s) — pacing between Chatwoot phone-search candidates (rate limiting, only hit on multi-candidate fallback)

**Result: none of these sit on the per-message webhook critical path** — they are bulk-sync/maintenance endpoints and deliberate API rate limiting. No sleeps removed or adjusted.

## Verification

```
C:\Users\amin\Desktop\wootify_instance_manager\.venv\Scripts\python.exe -m py_compile app/services/bridge_service.py app/adapters/bale_pv.py
```
Exit code 0, no stdout/stderr — both files compile cleanly.

## Notes / risks

- Concurrency is capped at 3 in-flight media sends per message; sends for different messages are not globally throttled (same as before).
- Behavioral change by design: previously one failing attachment failed the whole outbound message; now partial success is possible (message marked `sent` using the first successful attachment's platform response). This matches the requested `return_exceptions=True` semantics.
