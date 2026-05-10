# Wootify Connector

Wootify Connector is a FastAPI-based bridge between **Chatwoot** and messaging platforms (currently **Bale** and **Telegram**).  
It provides multi-instance routing, inbound polling, outbound webhook handling, conversation/message mapping, enterprise Bale flows, and a web admin UI.

## What This Project Does

- Receives outgoing Chatwoot webhook events and delivers them to Bale/Telegram.
- Polls Bale/Telegram for inbound updates and creates/updates Chatwoot conversations.
- Stores conversation and message mappings for reply threading and observability.
- Supports per-instance platform metadata, Chatwoot config, feature flags, and proxy config.
- Includes a dedicated Bale Enterprise flow with route-specific Chatwoot inboxes, live-session handling, enterprise manuals/catalogs, manual groups, GRE validation, and optional SMS sync.
- Exposes an API to manage connector instances and inspect mappings.

## Tech Stack

- Backend: Python 3.11+, FastAPI, SQLAlchemy, Alembic, HTTPX
- Database: SQLite or PostgreSQL
- Frontend: React + Vite (`wootify-instance-manager/`)
- Integrations: Chatwoot API, Bale Bot API, Telegram Bot API, Novin SMS API

## Repository Structure

```text
app/
  clients/        # External API clients (Chatwoot, Bale, Novin SMS)
  connectors/     # Platform connector implementations (Bale, Telegram) + registry
  controllers/    # FastAPI route handlers
  repositories/   # Data access layer (SQLAlchemy repositories)
  schemas/        # Pydantic request/response models
  services/       # Business flows (bridge, polling, instance management, enterprise)
  utils/          # Shared helpers (crypto, payload masking, media, logging, proxy, cache)
  main.py         # FastAPI entrypoint + lifespan
  models.py       # SQLAlchemy ORM models
  config.py       # Pydantic settings from environment
  db.py           # Database engine/session factory
alembic/          # Database migrations
docs/             # Project, API, and development documentation
scripts/          # One-off utilities (SQLite -> PostgreSQL migration)
wootify-instance-manager/  # React admin UI
```

## Quick Start

### 1) Backend setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

### 2) Configure environment

At minimum, set:

- `DATA_ENCRYPTION_KEY` (Fernet-compatible key)
- Database settings in `.env`
- Instance-level Chatwoot and platform tokens via the API/UI

Generate a key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Common database setups:

- SQLite:
  - leave `DATABASE_URL` as the default `sqlite:///.../wootify.db`
- PostgreSQL:
  - set `DATABASE_URL` to the server/credential URL, for example `postgresql+psycopg2://postgres:postgres@localhost:5432/`
  - set `DATABASE_NAME` to the target database name
  - leave `DATABASE_AUTO_CREATE=true` if you want the backend to create the database automatically on startup

The full documented template lives in `.env.example`.

### 3) Run migrations

```bash
alembic upgrade head
```

### 4) Start backend

```bash
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Health check: `http://localhost:8000/health`

### 5) Start frontend (optional, recommended)

```bash
cd wootify-instance-manager
npm install
npm run dev
```

Admin UI (dev): `http://localhost:5173`

Production-like UI build:

```bash
cd wootify-instance-manager
npm run build
```

Served by backend at: `http://localhost:8000/instance-manager`

## API Overview

Base path: `/api/v1`

- `GET /platform-types`
- `GET /features`
- `GET /instances`
- `POST /instances`
- `GET /instances/{instance_key}`
- `PATCH /instances/{instance_key}`
- `DELETE /instances/{instance_key}`
- `POST /instances/{instance_key}/chatwoot/inbox`
- `POST /instances/{instance_key}/enterprise/chatwoot/inboxes/{route_key}`
- `POST /webhooks/chatwoot/{instance_key}`
- `POST /webhooks/chatwoot/{instance_key}/enterprise/{route_key}`
- `POST /simulate/platform/{instance_key}`
- `GET /instances/{instance_key}/conversations`
- `GET /instances/{instance_key}/conversations/{conversation_id}`
- `GET /instances/{instance_key}/conversations/{conversation_id}/messages`
- `GET /instances/{instance_key}/enterprise/manuals`
- `POST /instances/{instance_key}/enterprise/manuals`
- `PATCH /instances/{instance_key}/enterprise/manuals/{asset_id}`
- `DELETE /instances/{instance_key}/enterprise/manuals/{asset_id}`
- `GET /instances/{instance_key}/enterprise/catalog`
- `PUT /instances/{instance_key}/enterprise/catalog`
- `DELETE /instances/{instance_key}/enterprise/catalog`
- `GET /instances/{instance_key}/enterprise/manual-groups`
- `POST /instances/{instance_key}/enterprise/manual-groups`
- `PUT /instances/{instance_key}/enterprise/manual-groups/{group_id}`
- `DELETE /instances/{instance_key}/enterprise/manual-groups/{group_id}`
- `GET /instances/{instance_key}/enterprise/manual-groups/{group_id}/manuals`
- `POST /instances/{instance_key}/enterprise/manual-groups/{group_id}/manuals/{asset_id}`
- `DELETE /instances/{instance_key}/enterprise/manual-groups/{group_id}/manuals/{asset_id}`
- `GET /instances/{instance_key}/enterprise/sessions`
- `GET /instances/{instance_key}/enterprise/sms-sync`
- `PATCH /instances/{instance_key}/enterprise/sms-sync`
- `POST /instances/{instance_key}/enterprise/sms-sync/run`

Full endpoint details: `docs/API_REFERENCE.md`

## Documentation Index

- Architecture: `docs/ARCHITECTURE.md`
- API reference: `docs/API_REFERENCE.md`
- Development guide: `docs/DEVELOPMENT.md`
- Commenting/docstring standards: `docs/COMMENTING_STANDARD.md`

## Open Source Collaboration

- Contribution workflow: `CONTRIBUTING.md`
- Code of conduct: `CODE_OF_CONDUCT.md`
- Security policy: `SECURITY.md`
- License: `LICENSE`

## Current Limitations

- No formal automated test suite yet (manual/integration testing is primary).
- PostgreSQL is supported, but deployers still need to manage backups, credentials, and operational monitoring themselves.

## SQLite to PostgreSQL Migration

If you already have data in SQLite and want to move to PostgreSQL:

1. Configure PostgreSQL in `.env`.
2. Run `alembic upgrade head` against the target database.
3. Run `python scripts/migrate_sqlite_to_postgres.py`.

The migration script uses `SQLITE_MIGRATION_SOURCE_URL` as the SQLite source and copies the current application tables into the configured PostgreSQL database.
