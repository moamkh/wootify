# API Reference

Base URL (local): `http://localhost:8000`  
API prefix: `/api/v1`

## Platform and Feature Metadata

### `GET /platform-types`

Returns active platform types with capabilities and metadata schema.

### `GET /features`

Returns feature definitions used for per-instance overrides.

## Instance Management

### `GET /instances`

List connector instances.

### `POST /instances`

Create a connector instance.

Minimal body example:

```json
{
  "instance_key": "support-telegram",
  "platform_type_key": "telegram",
  "is_enabled": true,
  "platform_metadata": {
    "telegram_token": "123456:ABCDEF"
  },
  "chatwoot": {
    "base_url": "http://localhost:3000",
    "api_access_token": "chatwoot_token",
    "account_id": 1,
    "inbox_name": "Telegram Support",
    "auto_create": true,
    "reopen_conversation": true
  },
  "feature_overrides": {
    "reply_sync": true,
    "media_sync": true
  }
}
```

When `chatwoot.auto_create` is `true`, the backend will make a best-effort attempt to create or discover the inbox
immediately after saving the instance. The response may include:

```json
{
  "auto_create_inbox": {
    "attempted": true,
    "created": true,
    "inbox_id": 12,
    "detail": "created"
  }
}
```

When `chatwoot.reopen_conversation` is `true`, inbound platform messages will reopen the mapped Chatwoot
conversation if Chatwoot currently reports it as `resolved`.

### `GET /instances/{instance_key}`

Get a single instance by key.

Instance responses include a derived Chatwoot webhook URL in `chatwoot.webhook_url`, for example:

```json
{
  "chatwoot": {
    "inbox_id": 12,
    "inbox_name": "Telegram Support",
    "webhook_url": "http://localhost:8000/api/v1/webhooks/chatwoot/support-telegram"
  }
}
```

### `PATCH /instances/{instance_key}`

Partially update an instance.

### `DELETE /instances/{instance_key}`

Delete an instance and related mappings/runtime state.

### `POST /instances/{instance_key}/chatwoot/inbox`

Create or discover Chatwoot inbox for that instance, then persist `inbox_id`.

## Sync Endpoints

### `POST /webhooks/chatwoot/{instance_key}`

Chatwoot outbound webhook ingestion endpoint.

### `POST /simulate/platform/{instance_key}`

Debug/simulation endpoint for inbound platform-like events.

## Mapping Explorer

### `GET /instances/{instance_key}/conversations`

List mapped conversations for an instance.

Optional query:

- `q`: substring filter on platform/chatwoot conversation ids

### `GET /instances/{instance_key}/conversations/{conversation_id}`

Get one mapped conversation.

### `GET /instances/{instance_key}/conversations/{conversation_id}/messages`

List mapped messages for a mapped conversation.

## Enterprise Assets

### `GET /instances/{instance_key}/enterprise/manuals`

List enterprise manual assets.

### `POST /instances/{instance_key}/enterprise/manuals`

Upload a manual PDF.

Required multipart form fields:

- `display_name`
- `link_url` (absolute `http(s)` URL)
- `file` (PDF)

### `GET /instances/{instance_key}/enterprise/catalog`

Get active catalog asset.

### `PUT /instances/{instance_key}/enterprise/catalog`

Upload or replace the catalog PDF.

Required multipart form fields:

- `link_url` (absolute `http(s)` URL)
- `file` (PDF)

Optional multipart form field:

- `display_name`

## Health

### `GET /health`

Service health endpoint.

## Notes for Integrators

- Private/non-outgoing Chatwoot events are ignored.
- Reply sync can skip messages when parent mapping is missing (depending on feature flags).
- Payload snapshots are persisted only when both:
  - feature `payload_debug_store` is enabled for the instance, and
  - env `STORE_MESSAGE_PAYLOADS=true`
