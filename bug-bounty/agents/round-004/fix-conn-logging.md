# Round 004 — SEC-04 fix: token leak via connector error logs

## Scope
- `app/connectors/bale_connector.py`
- `app/connectors/telegram_connector.py`
- No other files touched; no new files except this memory note.

## Root cause
Bot API URLs embed the token (`/bot<TOKEN>/<method>`). httpx exception
strings (notably `HTTPStatusError`, message format
`"Client error '404 ...' for url '.../bot<TOKEN>/sendMessage'"`) include the
full request URL, and error handlers passed `str(exc)` / `error_msg` directly
into log args.

## Fix
Added a local `_safe_error_message(exc)` helper to each connector class
(duplicated per instructions — no shared module):
returns `type(exc).__name__` plus ` status=<code>` when
`exc.response.status_code` exists. Never includes `str(exc)`.

Replaced `str(exc)` / `error_msg` with `self._safe_error_message(exc)` in
every error/exception **log** call:

### bale_connector.py
- `_request`: connect_error, timeout, http_error, request_error,
  unexpected_error log calls (method name + redacted `target` were already
  logged there; status already logged in http_error).
- `send_text` and `send_media` failure logs (error arg only).
- `get_updates` transport_error and generic failure logs (redacted target
  already present in transport_error log).
- `download_file_by_id` failure log.

### telegram_connector.py
- `_register_commands` warning log.
- `send_text` and `send_media` failure logs (error arg only).
- `get_updates` failure log.
- `download_file_by_id` failure log.

## Preserved (intentional, per "keep control flow identical")
- Log levels, format strings, `exc_info=True`, and all control flow.
- `raise RuntimeError(error_msg) from exc` in send_text/send_media still
  carries the raw message (wire/raise behavior unchanged).
- `get_updates` return dicts still include `description: str(exc)`
  (API response shape unchanged).

## Verification
- `python -m py_compile app/connectors/bale_connector.py app/connectors/telegram_connector.py` → exit 0, no output.
- Grep confirms no `str(exc)`/`error_msg` remains in any logger call in the
  two files; remaining occurrences are only in raise/return paths above.

## Residual risks (not fixed here — flagged for parent)
1. `exc_info=True` tracebacks still print the exception's own message as the
   final traceback line, so an `httpx.HTTPStatusError` traceback still
   contains the `/bot<TOKEN>/` URL. Removing `exc_info` was out of the
   delegated "keep log levels/control flow identical" mandate.
2. Raised `RuntimeError(error_msg)` and returned `description` values can
   still carry token-bearing strings to upstream callers that log them.
3. `app/connectors/bale_pv_connector.py` has similar `str(exc)` patterns but
   was outside the edit scope.
