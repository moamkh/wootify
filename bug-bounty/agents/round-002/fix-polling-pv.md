# Round 002 fixes — polling service + Bale PV connector performance batch

Files edited (only these two, per delegation scope):
- `app/services/bale_polling_service.py`
- `app/connectors/bale_pv_connector.py`

Verification: `python -m py_compile app/services/bale_polling_service.py app/connectors/bale_pv_connector.py`
(venv interpreter) → exit code 0, no output. Both files compile.

Line refs: "before" = pre-edit file, "after" = current file.

## 1. Removed hard-coded 0.5s sleep per update (bale_polling_service.py)
- Before: lines 222–223 — `# Rate-limit: small pause between updates...` + `await asyncio.sleep(0.5)` inside the per-update `for update in updates:` loop.
- After: both lines deleted; loop processes updates back-to-back (old line ~222 area).
- Effect: a batch of N updates no longer adds N×0.5s of pure sleep latency.

## 2. Post-cycle sleep now adaptive (bale_polling_service.py)
- Before: lines 347–350 — `await asyncio.wait_for(self._stop.wait(), timeout=int(poll_interval))` (5s) ran after EVERY iteration, even right after a busy batch.
- After: lines ~344–353 — if `updates` (the just-finished batch) is non-empty, `continue` immediately to drain backlog; otherwise `asyncio.wait_for(self._stop.wait(), timeout=1)` (1s, since the long-poll request itself paces the loop). Stop-event responsiveness preserved via `wait_for` on `self._stop`.
- Note: `updates` is always bound when this point is reached (assigned at old line 208 before the `not ok → continue` guard; all exception paths `continue`).

## 3. WEBP→JPEG Pillow conversion off the event loop (bale_polling_service.py)
- Before: line 937 — `converted, ext, converted_ct = BalePvAdapter._convert_webp(content)` ran blocking Pillow work on the event loop.
- After: lines ~941–944 — wrapped in `await asyncio.to_thread(BalePvAdapter._convert_webp, content)` with a comment. This was the only PIL call path in this file (the conversion itself lives in `app/adapters/bale_pv.py::_convert_webp`, which is outside edit scope; the adapter's own call path already used `asyncio.to_thread`).

## 4. Long-poll wait no longer clamped to 5s (bale_pv_connector.py)
- Before: line 1133 — `wait_seconds = min(timeout or 1, 5)` clamped the caller's 25s timeout to 5s.
- After: line 1146 — `wait_seconds = min(timeout or 1, 25)`. Push-queue waits now actually long-poll.

## 5+6. One reusable media AsyncClient instead of per-file clients (bale_pv_connector.py)
Implemented as a shared per-connector client:

- `__init__` (before lines 148–150): added `self._media_http_client: Optional[Any] = None` with comment.
- New method `_get_media_http_client()` (after lines ~157–174): lazily creates
  `httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(connect=10, read=120, write=120, pool=10))`
  and returns the cached instance. Local `import httpx` matches existing style (httpx is not imported at module top in this file).
- `send_media` media download (before lines 662–664): `async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:` → `client = self._get_media_http_client()` (after line 681); block dedented, now-unused local `import httpx` removed. Logging/error flow unchanged.
- `_upload_file_to_nasim` (before lines 762–765): `async with httpx.AsyncClient(follow_redirects=True, timeout=upload_timeout) as client:` → `client = self._get_media_http_client()` (after line 782). The whole former `async with` body (before lines 767–981) was dedented one level via an assertion-guarded mechanical dedent (no content changes). The set-cookie + GetNasimFileUploadUrl retry/preamble logic is untouched. To preserve the configurable `BALE_PV_MEDIA_UPLOAD_TIMEOUT_SECONDS` behavior (the shared client has a fixed timeout), `timeout=upload_timeout` is now passed per-request on the Nasim PUT (after line 940), keeping the existing timeout error message accurate.
- `download_file_by_id` (before line 1805): `async with httpx.AsyncClient(follow_redirects=True) as client:` (NO timeout → httpx default 5s, large files silently failed) → `client = self._get_media_http_client()` (after line 1822), so downloads now get read=120s (fix 5 subsumed by fix 6). Former body (before lines 1806–1944) dedented one level mechanically. Now-unused `import httpx` in that `try` block removed. set-cookie + GetNasimFileUrl(s) fallback logic untouched.
- `close()` (before lines 1954–1958): added `await self._media_http_client.aclose()` + reset to None (after lines ~1976–1978). Existing clean shutdown hook, so no dangling client.
- Out of scope, unchanged: the separate `httpx.AsyncClient()` at old line 2668 (a different, non-listed method) was not part of the delegated fix list.

## Constraints honored
- No new dependencies (httpx/asyncio only; local imports preserved).
- Per-call headers/auth flows (set-cookie, gRPC-web headers, retries) byte-identical.
- No other files touched.
