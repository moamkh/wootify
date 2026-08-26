# Wootify Connector

Wootify Connector is a FastAPI-based bridge between **Chatwoot** and messaging platforms (currently **Bale** and **Telegram**).  
It provides multi-instance routing, inbound polling, outbound webhook handling, conversation/message mapping, enterprise Bale flows, and a web admin UI.

## What This Project Does

- Receives outgoing Chatwoot webhook events and delivers them to Bale/Telegram.
- Polls Bale/Telegram for inbound updates and creates/updates Chatwoot conversations.
- Stores conversation and message mappings for reply threading and observability.
- Supports per-instance platform metadata, Chatwoot config, feature flags, and proxy config.
- Includes dedicated Enterprise flows for Bale and Telegram with route-specific Chatwoot inboxes, live-session handling, enterprise manuals/catalogs, and manual groups.
- Bale Enterprise adds GRE validation and optional external SMS sync; Telegram Enterprise uses dynamic routes and customizable menu labels with no GRE/SMS.
- Exposes an API to manage connector instances and inspect mappings.

## Tech Stack

- Backend: Python 3.11+, FastAPI, SQLAlchemy, Alembic, HTTPX
- Database: SQLite or PostgreSQL
- Frontend: React + Vite (`frontend/`)
- Integrations: Chatwoot API, Bale Bot API, Telegram Bot API, Novin SMS API (Bale Enterprise only)

## Repository Structure

```text
backend/
  src/wootify/
    bootstrap/     # App factory, dependency container, lifecycle
    domain/        # Stable domain values and platform capabilities
    application/   # Ports and composed messaging/enterprise policies
    plugins/       # Bale, Telegram, Bale PV, and experimental Instagram registry
    infrastructure/# SQLAlchemy models/sessions and filesystem adapters
    controllers/   # Compatibility facade over focused HTTP routers
  migrations/      # Alembic migrations
  tests/           # Backend unit, integration, and contract tests
frontend/          # Feature-oriented React admin UI
packages/
  bale-pv-client/  # Independently installable Bale PV protocol client
docs/              # Architecture, backlog, archive, and refactor audit
scripts/           # Migration, health, and architecture-audit tools
var/               # Ignored runtime databases, logs, sessions, assets, temp files
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
python -m uvicorn wootify.bootstrap.app:app --reload --host 0.0.0.0 --port 8000
```

The legacy `app.main:app` entrypoint remains supported for existing deployments.

Health check: `http://localhost:8000/health`

### 5) Start frontend (optional, recommended)

```bash
cd frontend
npm install
npm run dev
```

Admin UI (dev): `http://localhost:5173`

Production-like UI build:

```bash
cd frontend
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

- External integrations still require environment-specific end-to-end checks;
  automated backend, package, route-contract, and frontend build checks cover
  repository-owned behavior.
- PostgreSQL is supported, but deployers still need to manage backups, credentials, and operational monitoring themselves.

## SQLite to PostgreSQL Migration

If you already have data in SQLite and want to move to PostgreSQL:

1. Configure PostgreSQL in `.env`.
2. Run `alembic upgrade head` against the target database.
3. Run `python scripts/migrate_sqlite_to_postgres.py`.

The migration script uses `SQLITE_MIGRATION_SOURCE_URL` as the SQLite source and copies the current application tables into the configured PostgreSQL database.
