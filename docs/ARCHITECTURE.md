# Architecture

## High-Level Overview

Wootify Connector is a modular backend that synchronizes messages between Chatwoot and external messaging platforms.

Main direction flows:

1. Chatwoot -> Connector -> Bale/Telegram
2. Bale/Telegram -> Connector -> Chatwoot

It keeps persistent mappings for:

- Instance configuration and runtime state
- Conversation identity mapping
- Message identity + reply-parent mapping

## Runtime Components

- `app/main.py`
  - FastAPI app bootstrap
  - startup/shutdown lifecycle
  - polling service start/stop
  - API router mount
- `app/services/bale_polling_service.py`
  - poll manager per enabled instance
  - dispatches inbound updates to bridge service
- `app/services/bridge_service.py`
  - central orchestration for inbound/outbound sync
  - resolves destination identities, feature flags, and reply behavior
- `app/services/instance_service.py`
  - instance lifecycle and normalized decrypted runtime configuration

## Layering

- Controllers: HTTP-level concerns (`app/controllers/`)
- Services: business rules and orchestration (`app/services/`)
- Repositories: DB read/write abstraction (`app/repositories/`)
- Connectors: platform-specific transport (`app/connectors/`)
- Clients: external API wrappers (`app/clients/`)
- Schemas: API contracts (`app/schemas/`)

## Data Model Summary

Defined in `app/models.py`:

- `PlatformType`: registered platform capabilities + metadata schema
- `FeatureDefinition`: global feature definitions
- `Instance`: connector instance (platform/chatwoot/proxy config)
- `InstanceFeatureOverride`: per-instance feature toggles
- `InstanceRuntimeState`: polling/runtime checkpoint and last error
- `Conversation`: platform/chatwoot conversation mapping
- `ConversationRuntimeState`: per-conversation runtime values
- `MessageMapping`: message-level mapping for ids, status, and reply-parent links

## Inbound Flow (Platform -> Chatwoot)

1. Polling service reads updates from connector (`get_updates`).
2. Update is normalized into bridge event payload.
3. Bridge service resolves or creates mapped conversation.
4. Bridge service posts message/media to Chatwoot.
5. Message mapping is stored with `platform_to_chatwoot` direction.

## Outbound Flow (Chatwoot -> Platform)

1. Chatwoot sends webhook event to `/api/v1/webhooks/chatwoot/{instance_key}`.
2. Bridge service validates event type and instance status.
3. Conversation destination is resolved from mapping/contact/source metadata.
4. Connector sends text/media to platform.
5. Message mapping is stored with `chatwoot_to_platform` direction.

## Feature Flags and Safety Gates

Feature definitions are seeded by `PlatformRegistryService` and evaluated per instance:

- `reply_sync`
- `media_sync`
- `payload_debug_store`

`payload_debug_store` is hard-gated by environment variable:

- `STORE_MESSAGE_PAYLOADS=true`

## Security and Data Handling

- Instance/platform/chatwoot/proxy configuration is encrypted at rest using Fernet (`app/utils/crypto_utils.py`).
- Sensitive payload fields are masked before storage/logging (`app/utils/payload_utils.py`).
- Optional proxy routing is supported per instance (`app/utils/proxy_utils.py`).

