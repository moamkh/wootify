# Round 005 — Fix bridge outbound path

## Files edited
- `app/services/bridge_service.py`
- `app/services/message_mapping_service.py`

## Changes

### bridge_service.py (`receive_chatwoot_webhook`)

1. **CRITICAL — unconditional `access_hash` kwarg removed.**
   `send_media` and `send_text` were called with `access_hash=phone_access_hash`
   unconditionally, but only `BalePvConnector` accepts that kwarg -> TypeError on
   every outbound Bale/Telegram message (caught by the broad except, marked
   failed without attempting). Now the kwarg is only included when
   `platform_key == 'bale_pv_enterprise'` (same check style used at the phone
   resolution block), via `access_hash_kwargs` / `text_kwargs` dicts splatted
   into the calls.

2. **HIGH — caption/quoted only on first attachment.**
   `_send_attachment` now takes `index`; `caption=(content or None) if index == 0
   else None` and `quoted=quoted if index == 0 else None`. The gather call
   enumerates attachments. Per-attachment filename/type handling unchanged. A
   text+N-photo message no longer delivers the text N times or replies N times
   to the quoted parent.

3. **HIGH — partial attachment failure no longer masked as success.**
   Failed indexes are collected; if any failed but at least one succeeded, the
   successful sends are kept but an ERROR is logged
   (`outbound_media_partial_failure ... failed_indexes=...`) and a
   `RuntimeError('attachment send failed for indexes: [...]')` is raised so the
   existing failure branch upserts `status=failed` with the indexes in the
   detail — dedup will allow redelivery. Comment acknowledges succeeded
   attachments may duplicate on redelivery (acceptable). All-failed behavior
   unchanged (still raises `send_results[0]`).

4. **MED — CancelledError handled.**
   All `isinstance(..., Exception)` checks in the gather-results block changed
   to `BaseException` (failure logger, first_result fallback picker). A
   cancelled result can no longer be picked as the "successful"
   platform_response and crash at `.get('id')`.

### bridge_service.py (`_resolve_operator_notification`)

6. **LOW — first-ever operator note is a proper sentence.**
   `return resolved_name, row, resolved_name` ->
   `return f'Operator {resolved_name} joined the conversation.', row, resolved_name`,
   matching the English tone of the nearby `f'Operator changed: {resolved_name}'`.

### message_mapping_service.py (`upsert`)

5. **MED — `platform_message_id` no longer clobbered by failure upserts.**
   `row.platform_message_id = str(platform_message_id) if platform_message_id
   else None` replaced with: only assign when a non-None value is passed
   (`if platform_message_id is not None: row.platform_message_id =
   str(platform_message_id)`), preserving the existing row value on
   omitted/None calls (e.g. the bridge failure branch). Create path verified:
   a fresh `MessageMapping` row keeps the column default of None, and success
   upserts still pass a non-None id which is written as before.

## Verification
```
.venv\Scripts\python.exe -m py_compile app/services/bridge_service.py app/services/message_mapping_service.py
```
Exit code 0, no output — both files compile clean.
