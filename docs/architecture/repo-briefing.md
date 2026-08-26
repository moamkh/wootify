# Repo Briefing: `wootify_instance_manager`

## What this project is
Wootify Instance Manager is a FastAPI backend plus a React admin UI for managing Chatwoot connector instances. It supports generic Bale/Telegram bridge flows and a dedicated Bale Enterprise mode with live Chatwoot routing, enterprise asset delivery, and optional external SMS synchronization.

## High-signal files/folders to read
- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/API_REFERENCE.md`
- `docs/DEVELOPMENT.md`
- `.env.example`
- `app/main.py`
- `app/controllers/api_v1_controller.py`
- `app/services/bridge_service.py`
- `app/services/enterprise_bale_service.py`

## Tech signals
- Backend: Python, FastAPI, SQLAlchemy, Alembic, HTTPX
- Frontend: React + Vite (`wootify-instance-manager/`)
- Database backends:
  - SQLite for simple local setups
  - PostgreSQL via `psycopg2` for persistent multi-user deployments
- External integrations:
  - Chatwoot REST API + webhooks
  - Bale Bot API
  - Telegram Bot API
  - Optional Novin enterprise SMS source

## Top-level layout
- `app/`: backend application code
- `alembic/`: schema migrations
- `docs/`: architecture, API, and development documentation
- `scripts/`: one-off maintenance/migration utilities
- `wootify-instance-manager/`: React admin UI
- `.env.example`: documented runtime configuration template

## Likely entrypoints
- `app/main.py`: FastAPI app bootstrap and lifespan hooks
- `app/controllers/api_v1_controller.py`: HTTP API surface
- `app/services/bale_polling_service.py`: inbound Bale polling loop
- `app/services/telegram_polling_service.py`: inbound Telegram polling loop

## Main flows to understand
1. Generic outbound sync: Chatwoot webhook -> controller -> `BridgeService` -> connector -> Bale/Telegram.
2. Generic inbound sync: poller -> connector update -> `BridgeService` -> Chatwoot message/conversation APIs.
3. Enterprise live routing: Bale enterprise update/webhook -> `EnterpriseBaleService` -> Chatwoot enterprise inbox/conversation/contact APIs.
4. Enterprise SMS sync: scheduler/manual API call -> `EnterpriseBaleService.sync_external_sms_messages()` -> Bale delivery.

## Current database story
- The backend resolves its runtime DB URL from `.env`.
- PostgreSQL setups use:
  - `DATABASE_URL` for the server/credentials
  - `DATABASE_NAME` for the target database name
- On startup, `app/db.py` can auto-create the Postgres database when `DATABASE_AUTO_CREATE=true`.
- `scripts/migrate_sqlite_to_postgres.py` can copy an existing SQLite dataset into the configured PostgreSQL database.

## Practical next steps
- Read `.env.example` before changing deployment settings.
- Verify the active DB with `python -c "from app.config import settings; print(settings.resolved_database_url)"`.
- Trace one enterprise route flow through `api_v1_controller.py` and `enterprise_bale_service.py` before changing live-chat behavior.
