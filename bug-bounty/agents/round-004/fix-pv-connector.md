# Round 4 — Fix: app/connectors/bale_pv_connector.py

Date: 2026-08-10
File edited: `app/connectors/bale_pv_connector.py` (only)

## 1. HIGH — Cross-tenant media auth via shared cookie jar
- Replaced singleton `self._media_http_client: Optional[Any]` with
  `self._media_http_clients: Dict[str, Any]` keyed by instance key.
- `_get_media_http_client(instance)` now creates one lazily-built
  `httpx.AsyncClient` per instance (same config: `follow_redirects=True`,
  `Timeout(connect=10, read=120, write=120, pool=10)`), so each instance's
  `set-cookie` session cookies live in an isolated jar.
- Threaded the instance key through all three call sites:
  `send_media` (source-URL download), `_upload_file_to_nasim`,
  `download_file_by_id`.
- `disconnect(instance)` pops and `aclose()`es that instance's media client
  (done before the runtime early-return so orphaned clients are still cleaned
  up). `close()` closes any remaining clients after disconnecting all
  instances.

## 2. MED — Upload PUT scalar timeout replaced all four components
- `client.put(upload_url, ..., timeout=upload_timeout)` now passes
  `httpx.Timeout(connect=10, read=upload_timeout, write=upload_timeout, pool=10)`
  so the upload budget applies to read/write without shrinking read/write from
  120→60 or growing connect 10→60.

## 3. HIGH — `bale_pv_session_dir` path traversal / arbitrary write
- Added `_validate_session_dir(session_dir)` static helper: rejects any path
  containing `..` components, resolves the candidate (relative paths resolve
  against the repo root so the default keeps working regardless of CWD), and
  requires containment under `<repo_root>/data` via `is_relative_to`;
  raises `ValueError` with a clear message otherwise.
- `connect()` now runs the configured dir through this validator before
  `mkdir`/JWT file writes. Default `./data/bale_pv_sessions` resolves to
  `<repo_root>/data/bale_pv_sessions` and passes.

## 4. LOW — `_normalize_bale_phone` dead branch / missing 00-prefix
- Removed dead `elif digits.startswith("+")` branch (`re.sub(r"\D", "", ...)`
  already strips `+`).
- Added `00` international-prefix handling: leading `00` is stripped so
  `00989136421196` → `989136421196`.

## Verification
- `py_compile` via `.venv\Scripts\python.exe -m py_compile` — exit 0.
- Smoke test (venv python):
  - `_normalize_bale_phone`: `09136421196`, `+989136421196`, `00989136421196`
    all → `989136421196`.
  - `_validate_session_dir`: default `./data/bale_pv_sessions` accepted
    (resolves under repo `data\`); `../evil`, `data/../../etc`, `/etc/passwd`,
    `C:\Windows\temp` all rejected with `ValueError`.
- Grep confirms no remaining references to the old singular
  `_media_http_client` attribute (only the renamed method and dict remain).

## Notes / residual risk
- `get_contacts` already uses a per-call `httpx.AsyncClient` context manager;
  no shared-jar exposure there, left unchanged.
- Per-instance clients mean N idle connections for N instances; bounded by
  disconnect/close cleanup.
