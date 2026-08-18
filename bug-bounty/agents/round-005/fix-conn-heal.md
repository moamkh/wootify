# Round 5 — fix-conn-heal: connector availability bugs

**Scope:** `app/connectors/bale_connector.py`, `app/connectors/telegram_connector.py` only.

## Bug 1 (HIGH) — stale runtime never heals after a failed recreate

**Symptom:** after a failed `_create_runtime`, `self._instances[instance]` could keep pointing at a runtime whose httpx clients are closed. On the next `connect()` with an unchanged cfg, the same-cfg early-return handed back the broken runtime, and every subsequent op failed forever with `Cannot send a request, as the client has been closed`.

**Fix (both connectors):**
- `connect()`: the same-cfg early-return now additionally verifies the runtime's clients are not closed.
  - Bale: `not existing.client.is_closed and not existing.file_client.is_closed` — falls through to recreate if either is closed.
  - Telegram: new static helper `_runtime_clients_closed(runtime)` checks `file_client.is_closed` and each `_client` of the bot's internal `HTTPXRequest` objects (`bot._request`), guarded with `getattr` since those are PTB-internal attributes.
- Recreate wrapped in `try/except`: on failure the poisoned entry is removed from `self._instances` (`pop(instance, None)`) and the exception re-raised, so the next `connect()` retries cleanly.

## Bug 2 (MED) — timeout mismatch vs long-poll

**Symptom:** flat `timeout=30` on httpx clients while long-poll `getUpdates` waits up to 25s (`*_LONG_POLL_TIMEOUT_SECONDS`) — only 5s margin; and a 30s connect timeout is far too patient for dead hosts.

**Fix:**
- Bale `_create_runtime`:
  - API client: `httpx.Timeout(connect=10, read=int(settings.BALE_LONG_POLL_TIMEOUT_SECONDS) + 15, write=30, pool=10)` (read = 40s at defaults).
  - File client: `httpx.Timeout(connect=10, read=120, write=30, pool=10)`.
- Telegram `_create_runtime`:
  - `get_updates_request` (HTTPXRequest): `read_timeout=float(settings.TELEGRAM_LONG_POLL_TIMEOUT_SECONDS) + 15.0` (40s at defaults), `connect_timeout=10.0`, `write_timeout=30.0`, `pool_timeout=10.0`.
  - Regular request: `connect_timeout` 5→10, `write_timeout` 10→30, `pool_timeout` 1→10, read kept at 30 (no long-poll on this client).
  - `file_client`: `httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)`.

## Verification

- `C:\Users\amin\Desktop\wootify_instance_manager\.venv\Scripts\python.exe -m py_compile app/connectors/bale_connector.py app/connectors/telegram_connector.py` → exit 0, no output.
- Note: the brief described telegram's API client as raw httpx `timeout=30`; in fact telegram uses python-telegram-bot `HTTPXRequest` (read 35/write 10/connect 5/pool 1). The analogous fix was applied to the HTTPXRequest parameters instead.
