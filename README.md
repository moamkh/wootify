# Wootify Connector

Wootify Connector is a FastAPI-based bridge between **Chatwoot** and messaging platforms (currently **Bale** and **Telegram**).  
It provides multi-instance routing, inbound polling, outbound webhook handling, conversation/message mapping, and a web admin UI.

## What This Project Does

- Receives outgoing Chatwoot webhook events and delivers them to Bale/Telegram.
- Polls Bale/Telegram for inbound updates and creates/updates Chatwoot conversations.
- Stores conversation and message mappings for reply threading and observability.
- Supports per-instance platform metadata, Chatwoot config, feature flags, and proxy config.
- Exposes an API to manage connector instances and inspect mappings.

## Tech Stack

- Backend: Python, FastAPI, SQLAlchemy, Alembic, HTTPX
- Database: SQLite (default)
- Frontend: React + Vite (`wootify-instance-manager/`)
- Integrations: Chatwoot API, Bale Bot API, Telegram Bot API

## Repository Structure

```text
app/
  clients/        # External API clients (Chatwoot, Bale)
  connectors/     # Platform connector implementations (Bale, Telegram)
  controllers/    # FastAPI route handlers
  repositories/   # Data access layer (SQLAlchemy repositories)
  schemas/        # Pydantic request/response models
  services/       # Business flows (bridge, polling, instance management)
  utils/          # Shared helpers (crypto, payload masking, media, logging)
  main.py         # FastAPI entrypoint + lifespan
  models.py       # SQLAlchemy ORM models
alembic/          # Database migrations
docs/             # Project, API, and development documentation
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
- Instance-level Chatwoot and platform tokens via the API/UI

Generate a key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3) Run migrations

```bash
alembic upgrade head
```

### 4) Start backend

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
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
- `POST /webhooks/chatwoot/{instance_key}`
- `POST /simulate/platform/{instance_key}`
- `GET /instances/{instance_key}/conversations`
- `GET /instances/{instance_key}/conversations/{conversation_id}`
- `GET /instances/{instance_key}/conversations/{conversation_id}/messages`

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
- SQLite is the default storage for local/dev; production hardening is the deployer's responsibility.
