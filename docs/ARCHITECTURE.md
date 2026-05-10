# Architecture

## High-Level Overview

Wootify Connector is a modular backend that synchronizes messages between Chatwoot and external messaging platforms.

Main direction flows:

1. Chatwoot -> Connector -> Bale/Telegram
2. Bale/Telegram -> Connector -> Chatwoot

There are three major runtime modes:

- Generic bridge mode for standard Bale/Telegram instances
- Enterprise Bale mode for route-specific live support/sales sessions, enterprise document delivery, manual groups, GRE validation, and optional external SMS sync
- Enterprise Telegram mode for dynamic route-based live sessions, enterprise document delivery, manual groups, and customizable menu labels (no GRE validation, no SMS sync)

It keeps persistent mappings for:

- Instance configuration and runtime state
- Conversation identity mapping
- Message identity + reply-parent mapping
- Enterprise user state, sessions, pending messages, and document assets

## Runtime Components

- `app/main.py`
  - FastAPI app bootstrap
  - startup/shutdown lifecycle (logging, DB seed, polling start)
  - API router mount + static files for the admin UI
  - Global exception handlers
- `app/services/bale_polling_service.py`
  - poll manager per enabled instance
  - dispatches inbound updates to bridge service or enterprise service
- `app/services/bridge_service.py`
  - central orchestration for inbound/outbound sync
  - resolves destination identities, feature flags, and reply behavior
  - handles contact/conversation reuse, operator-change notifications, and deleted-conversation recovery
- `app/services/enterprise_bale_service.py`
  - enterprise Bale runtime orchestration
  - handles live-route session state, Chatwoot route inboxes, enterprise assets, manual groups, GRE validation, and SMS sync
- `app/services/enterprise_telegram_service.py`
  - enterprise Telegram runtime orchestration
  - handles dynamic routes, live-session state, Chatwoot route inboxes, enterprise assets, manual groups, and customizable labels
- `app/services/instance_service.py`
  - instance lifecycle and normalized decrypted runtime configuration
  - feature override computation and webhook URL building
- `app/services/platform_registry_service.py`
  - seeds platform types and default feature definitions on startup
- `app/services/conversation_mapping_service.py`
  - CRUD wrapper for platform↔Chatwoot conversation mappings
- `app/services/message_mapping_service.py`
  - CRUD wrapper for message mappings and reply-parent resolution
- `app/services/enterprise_document_service.py`
  - upload/replace/delete enterprise PDF assets (manuals & catalog)
- `app/services/enterprise_manual_group_service.py`
  - CRUD for manual groups and group↔manual assignments
- `app/services/enterprise_gre_service.py`
  - GRE phone eligibility validation via internal API

## Layering

- Controllers: HTTP-level concerns (`app/controllers/`)
- Services: business rules and orchestration (`app/services/`)
- Repositories: DB read/write abstraction (`app/repositories/`)
- Connectors: platform-specific transport (`app/connectors/`)
- Clients: external API wrappers (`app/clients/`)
- Schemas: API contracts (`app/schemas/`)
- Utils: shared helpers (`app/utils/`)
  - `crypto_utils.py` — Fernet encryption for config-at-rest
  - `payload_utils.py` — sensitive field masking before storage/logging
  - `proxy_utils.py` — optional proxy routing per instance
  - `media_utils.py` — media type detection and processing helpers
  - `logging_utils.py` — structured logging helpers
  - `cache.py` — lightweight in-memory caching utilities

## Data Model Summary

Defined in `app/models.py`:

- `PlatformType`: registered platform capabilities + metadata schema
- `FeatureDefinition`: global feature definitions
- `Instance`: connector instance (platform/chatwoot/proxy config, encrypted at rest)
- `InstanceFeatureOverride`: per-instance feature toggles
- `InstanceRuntimeState`: polling/runtime checkpoint and last error
- `Conversation`: platform ↔ Chatwoot conversation mapping
- `ConversationRuntimeState`: per-conversation runtime values (e.g. last operator name)
- `MessageMapping`: message-level mapping for ids, status, and reply-parent links
- `EnterpriseBaleUser`: per-user enterprise state, phone number, and GRE status
- `EnterpriseBaleSession`: enterprise live route sessions tied to Chatwoot contacts/conversations
- `EnterprisePendingMessage`: operator messages queued while the enterprise user is away from the live session
- `EnterpriseTelegramUser`: per-user Telegram enterprise state with dynamic string-based state (no GRE, no phone required)
- `EnterpriseTelegramSession`: Telegram enterprise live route sessions
- `EnterpriseTelegramPendingMessage`: operator messages queued for Telegram enterprise users
- `EnterpriseDocumentAsset`: stored manuals / catalog PDFs
- `EnterpriseManualGroup`: grouping/category for manuals
- `EnterpriseManualGroupAssignment`: many-to-many link between groups and assets

## Connectors and Registry

- `app/connectors/base_connector.py` — `PlatformConnector` protocol
- `app/connectors/registry.py` — `ConnectorRegistry` singleton mapping platform types to connectors
- `app/connectors/bale_connector.py` — `BaleBotConnector` via raw HTTPX
- `app/connectors/telegram_connector.py` — `TelegramBotConnector` via `python-telegram-bot`

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

## Enterprise Bale Flow

1. Bale Enterprise polling sends updates to `EnterpriseBaleService.handle_platform_update()`.
2. The service resolves the enterprise user, GRE status, and current menu/live-session state.
3. For live routes, the service forwards customer messages into route-specific Chatwoot conversations.
4. For operator replies, Chatwoot hits `/api/v1/webhooks/chatwoot/{instance_key}/enterprise/{route_key}`.
5. The enterprise service delivers the accepted notice and operator payload back to Bale, or queues it if the user already left the live session.
6. If Chatwoot contact/conversation IDs are stale, the service recreates the route session before retrying the forward path.
7. Pending messages are flushed when the user returns to a live session.
8. Optional SMS sync polls an external provider and forwards matching SMS to enterprise users.

## Enterprise Telegram Flow

1. Telegram Enterprise polling sends updates to `EnterpriseTelegramService.handle_platform_update()`.
2. The service resolves the enterprise user (no GRE validation needed) and current dynamic state.
3. Root menu buttons are built dynamically from `enterprise_routes`, plus catalog/manuals/address buttons with customizable labels.
4. For live routes, the service forwards customer messages into the route's Chatwoot conversation.
5. For operator replies, Chatwoot hits the route-specific webhook URL.
6. The enterprise service delivers the accepted notice and operator payload back to Telegram, or queues it if the user left the live session.
7. Dynamic routes are fully configurable per instance via `enterprise_routes` metadata.

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
- Log redaction of secrets is enabled by default (`LOG_REDACT_SECRETS=true`).

## Database Bootstrapping

- The active runtime database URL is resolved in `app/config.py`.
- `app/db.py` supports both SQLite and PostgreSQL.
- When PostgreSQL is configured and `DATABASE_AUTO_CREATE=true`, startup performs a best-effort `CREATE DATABASE` against `POSTGRES_ADMIN_DATABASE` before creating the main engine.
- Alembic migrations always run against the resolved runtime database URL.
- On first startup, `Base.metadata.create_all` ensures tables exist even before alembic is run.
