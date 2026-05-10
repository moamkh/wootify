# API Reference

Base URL (local): `http://localhost:8000`  
API prefix: `/api/v1`

## Platform and Feature Metadata

### `GET /platform-types`

Returns active platform types with capabilities and metadata schema.

Supported types:
- `bale`
- `bale_enterprise`
- `telegram`

### `GET /features`

Returns feature definitions used for per-instance overrides.

Built-in features:
- `reply_sync` — default `true`
- `media_sync` — default `true`
- `payload_debug_store` — default `false` (gated by `STORE_MESSAGE_PAYLOADS=true`)

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

When `chatwoot.auto_create` is `true`, the backend will make a best-effort attempt to create or discover the inbox immediately after saving the instance. The response may include:

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

For `bale_enterprise` instances, `platform_metadata` may also include:
- `enterprise_customer_service_auto_create`
- `enterprise_sales_auto_create`

Which trigger route inbox creation and return `enterprise_auto_create_inboxes`.

When `chatwoot.reopen_conversation` is `true`, inbound platform messages will reopen the mapped Chatwoot conversation if Chatwoot currently reports it as `resolved`.

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

Partially update an instance. Supports the same auto-create behaviors as `POST /instances`.

### `DELETE /instances/{instance_key}`

Delete an instance and related mappings/runtime state.

### `POST /instances/{instance_key}/chatwoot/inbox`

Create or discover Chatwoot inbox for that instance, then persist `inbox_id`. Not available for `bale_enterprise` instances (use the enterprise route inbox endpoint instead).

## Sync Endpoints

### `POST /webhooks/chatwoot/{instance_key}`

Chatwoot outbound webhook ingestion endpoint.

### `POST /webhooks/chatwoot/{instance_key}/enterprise/{route_key}`

Route-specific Chatwoot webhook endpoint for Bale Enterprise route inboxes.

Supported `route_key` values:

- `customer_service`
- `sales`

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
- `file` (PDF)

Optional multipart form field:

- `link_url` (absolute `http(s)` URL)

### `PATCH /instances/{instance_key}/enterprise/manuals/{asset_id}`

Patch manual metadata (`display_name` and/or `link_url`).

### `DELETE /instances/{instance_key}/enterprise/manuals/{asset_id}`

Delete a manual asset.

### `GET /instances/{instance_key}/enterprise/catalog`

Get active catalog asset.

### `PUT /instances/{instance_key}/enterprise/catalog`

Upload or replace the catalog PDF.

Required multipart form fields:

- `link_url` (absolute `http(s)` URL)
- `file` (PDF)

Optional multipart form field:

- `display_name`

### `DELETE /instances/{instance_key}/enterprise/catalog`

Delete the active catalog asset.

## Enterprise Manual Groups

### `GET /instances/{instance_key}/enterprise/manual-groups`

List enterprise manual groups.

### `POST /instances/{instance_key}/enterprise/manual-groups`

Create an enterprise manual group.

Body:

```json
{
  "name": "Group Name"
}
```

### `PUT /instances/{instance_key}/enterprise/manual-groups/{group_id}`

Rename an enterprise manual group.

Body:

```json
{
  "name": "New Group Name"
}
```

### `DELETE /instances/{instance_key}/enterprise/manual-groups/{group_id}`

Delete an enterprise manual group.

### `GET /instances/{instance_key}/enterprise/manual-groups/{group_id}/manuals`

List manuals assigned to a group.

### `POST /instances/{instance_key}/enterprise/manual-groups/{group_id}/manuals/{asset_id}`

Assign a manual to a group.

### `DELETE /instances/{instance_key}/enterprise/manual-groups/{group_id}/manuals/{asset_id}`

Remove a manual from a group.

## Enterprise Live Routing

### `POST /instances/{instance_key}/enterprise/chatwoot/inboxes/{route_key}`

Create or discover a route-specific Chatwoot API inbox for a Bale Enterprise instance.

Supported `route_key` values:

- `customer_service`
- `sales`

### `GET /instances/{instance_key}/enterprise/sessions`

List enterprise live-chat sessions and their current route/contact/conversation state.

## Enterprise SMS Sync

### `GET /instances/{instance_key}/enterprise/sms-sync`

Get the instance-level SMS sync configuration used by the Bale Enterprise flow.

### `PATCH /instances/{instance_key}/enterprise/sms-sync`

Patch instance-level SMS sync configuration.

Patch fields:

- `enabled`
- `api_url`
- `api_token`
- `token_header`
- `token_prefix`
- `poll_interval_minutes`
- `last_id`
- `http_timeout_seconds`

### `POST /instances/{instance_key}/enterprise/sms-sync/run`

Run an immediate SMS synchronization cycle and return counters for fetched, delivered, dropped, and failed records.

## Health

### `GET /health`

Service health endpoint.

## Notes for Integrators

- Private/non-outgoing Chatwoot events are ignored.
- Reply sync can skip messages when parent mapping is missing (depending on feature flags).
- Payload snapshots are persisted only when both:
  - feature `payload_debug_store` is enabled for the instance, and
  - env `STORE_MESSAGE_PAYLOADS=true`
- Chatwoot webhooks include a fallback resolver that matches by `inbox_id` or `inbox_name` when the requested `instance_key` does not resolve directly.
