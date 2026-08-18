# Round 002 — Fix: PV Bridge Performance Bugs

**Date:** 2026-08-09
**Scope:** `app/services/chatwoot_bridge_service.py`, `app/clients/chatwoot_client.py` (only these two files touched)

## Fix 1 — Per-message ChatwootClient construction → cached per-instance client

**Problem:** `_chatwoot_client_for_instance` built a brand-new `ChatwootClient`
(new `httpx.AsyncClient`, never closed) for every inbound message — no TCP/TLS
keep-alive reuse and a connection-pool leak.

**Change:** Mirrored the TTLCache pattern from `BridgeService`
(`app/services/bridge_service.py` ~line 46, 2118-2128):

- Added `from app.utils.cache_utils import TTLCache` import.
- Added `ChatwootBridgeService.__init__` with
  `self._clients: TTLCache[ChatwootClient] = TTLCache(maxsize=50, ttl=3600)`.
- Added `_get_chatwoot_client(chatwoot_cfg)`: keys clients by
  `f"{base_url}::{token}"`, creates with `timeout=30` (same as before) on miss.
- `_chatwoot_client_for_instance` now returns the cached client.

Note: `BridgeService` also never closes evicted/expired clients (the TTLCache
has no close hook), so no prune/close handling was copied — behavior matches
the existing pattern exactly. The service is a module-level singleton
(`chatwoot_bridge = ChatwootBridgeService()`), so the cache is effectively
module-level as required.

## Fix 2 — Skip remote contact/conversation lookups when a local mapping exists

**Problem:** Every message made 2 sequential remote GETs
(`GET contacts/search` + `GET contacts/{id}/conversations`) even though the
service already persists mappings in the local `Conversation` row
(`platform_conversation_id` = chat_id, `chatwoot_contact_id`,
`chatwoot_conversation_id`, `is_active`).

**Changes:**

- `_get_or_create_contact`: new optional kwargs `db`/`instance`. When provided,
  it first queries the local `Conversation` row for
  `(instance_id, platform_conversation_id=chat_id)` (no `is_active` filter — a
  contact stays valid even if its conversation was resolved) and returns the
  persisted `chatwoot_contact_id` directly, skipping the remote
  `contacts/search` call. Both call sites in `ingest_platform_event` (main
  contact and group sender contact) now pass `db=db, instance=instance`.
  New-contact creation flow is unchanged.
- `_get_or_create_conversation`: removed the per-message remote status check
  (`_get_remote_conversation_status`, which issued
  `GET contacts/{id}/conversations`). An active local mapping with a
  `chatwoot_conversation_id` is now returned directly.

**Why this is safe (no schema changes, no other files touched):**

- Resolved/closed conversations are already marked `is_active = False` locally
  by the `conversation_status_changed` webhook handler
  (`_handle_conversation_status_change`), so the local mapping stays fresh.
- A remotely deleted conversation/contact surfaces as a Chatwoot 404 when
  posting; the existing `_is_missing_chatwoot_conversation` →
  `_recreate_chatwoot_conversation` path in `ingest_platform_event` recreates
  the conversation and refreshes the local mapping via
  `_ensure_local_conversation`.

`_get_remote_conversation_status` is now unused but kept (harmless helper,
minimal diff).

**Expected effect:** steady-state inbound message goes from 3 sequential
Chatwoot calls (~2.6s) to 1 (the message POST), plus keep-alive reuse.

## Fix 3 — Status-code retry gating for non-idempotent posts

**Problem:** `ChatwootClient._request` retried 429/502/503/504 regardless of
`retry_on_read_errors`, so `post_message` / `post_message_with_attachments`
(which pass `retry_on_read_errors=False`) could be re-sent on those statuses —
duplicate messages and full multipart re-transmission of attachments.

**Change:** The transient-status retry branch now also requires
`retry_on_read_errors` (the same flag gating read-error retries, which callers
use to mark idempotency):

```python
if (
    resp.status_code in (429, 502, 503, 504)
    and retry_on_read_errors
    and attempt < 2
):
```

With the flag False the response falls straight through to
`resp.raise_for_status()` and the error propagates immediately — no retry,
no duplicate.

## Verification

```
.venv\Scripts\python.exe -m py_compile app/services/chatwoot_bridge_service.py app/clients/chatwoot_client.py
# exit code 0, no output
```

## Post-change coordination check (fix-bot-bridge touched bridge_service.py)

Re-inspected `app/services/bridge_service.py` after the fix-bot-bridge agent's
concurrent changes: the TTLCache pattern is unchanged
(`self._clients: TTLCache[ChatwootClient] = TTLCache(maxsize=50, ttl=3600)` in
`__init__`, `_get_chatwoot_client` keyed by `base_url::token`), so the mirrored
pattern in `ChatwootBridgeService` remains accurate. Re-ran
`py_compile` on all three files (`bridge_service.py`,
`chatwoot_bridge_service.py`, `chatwoot_client.py`) → exit 0. No conflicts;
no edits by this agent were needed.

## Deferrals

None — all three fixes implemented. Fix 2 required no schema changes and no
edits outside the two allowed files.
