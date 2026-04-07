# Development Guide

## Prerequisites

- Python 3.11+ (tested with modern Python 3.x)
- Node.js 18+ (for admin UI)
- npm (or compatible package manager)

## Backend Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Frontend Setup

```bash
cd wootify-instance-manager
npm install
npm run dev
```

## Core Environment Variables

From `.env.example`:

- `SERVER_BASE_URL`: backend public URL used in webhook/inbox wiring.
- `DATA_ENCRYPTION_KEY`: Fernet key for encrypted config-at-rest.
- `STORE_MESSAGE_PAYLOADS`: enables payload persistence gate (with feature flag).
- `DATABASE_URL`: optional override for SQLite path.
- `CHATWOOT_*`: default Chatwoot behavior.
- `BALE_*`, `TELEGRAM_*`: platform defaults for polling and UX prompts.
- `LOG_*`: logging format, level, redaction, and color options.

## Database and Migrations

- ORM models: `app/models.py`
- Migration env: `alembic/env.py`
- Migration scripts: `alembic/versions/`

Run latest migrations:

```bash
alembic upgrade head
```

Create a migration (after model changes):

```bash
alembic revision --autogenerate -m "describe change"
```

## Documentation and Comments

- Follow `docs/COMMENTING_STANDARD.md`.
- Keep docstrings practical and behavior-oriented.
- Keep comments in English for maintainability.

## Manual Test Checklist

1. Create instance via API/UI.
2. Verify inbox creation in Chatwoot.
3. Send outbound message from Chatwoot to platform.
4. Send inbound platform message and verify Chatwoot conversation mapping.
5. Test media sync, reply sync, and status notifications.
6. Review logs for errors/redaction correctness.

## Troubleshooting

- `Failed to decrypt config payload`:
  - `DATA_ENCRYPTION_KEY` changed after data was written.
- `database is locked`:
  - usually transient under SQLite; service retries runtime-state updates.
- Missing platform token errors:
  - ensure correct token key inside `platform_metadata`:
    - Bale: `bale_token`
    - Telegram: `telegram_token`

