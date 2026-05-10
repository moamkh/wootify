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
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
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
- `DATABASE_URL`: base SQLAlchemy connection URL. For PostgreSQL, this should usually omit the final database name and point at the server itself.
- `DATABASE_NAME`: target PostgreSQL database name appended to `DATABASE_URL`.
- `DATABASE_AUTO_CREATE`: when `true`, startup will create the configured PostgreSQL database if it does not exist yet.
- `POSTGRES_ADMIN_DATABASE`: admin database used only for the `CREATE DATABASE` step.
- `SQLITE_MIGRATION_SOURCE_URL`: source SQLite database used by the one-time PostgreSQL migration script.
- `SQLITE_BUSY_TIMEOUT_MS`: SQLite busy timeout (default 30000).
- `SQLITE_JOURNAL_MODE`: SQLite journal mode (default WAL).
- `CHATWOOT_*`: default Chatwoot behavior, status messages, and notifications.
- `BALE_*`, `TELEGRAM_*`: platform defaults for polling, UX prompts, and share-phone buttons.
- `ENTERPRISE_SMS_*`: defaults for Bale Enterprise SMS synchronization.
- `LOG_*`: logging format, level, redaction, HTTP request logging, and color options.

## Database and Migrations

- ORM models: `app/models.py`
- Migration env: `alembic/env.py`
- Migration scripts: `alembic/versions/`

Run latest migrations:

```bash
alembic upgrade head
```

Typical local database modes:

- SQLite:
  - keep the default `DATABASE_URL=sqlite:///.../wootify.db`
- PostgreSQL:
  - set `DATABASE_URL=postgresql+psycopg2://user:password@host:5432/`
  - set `DATABASE_NAME=your_database_name`
  - keep `DATABASE_AUTO_CREATE=true` if you want the backend to create the DB automatically

Move existing SQLite data into PostgreSQL:

```bash
python scripts/migrate_sqlite_to_postgres.py
```

Check which database URL the backend will actually use:

```bash
python -c "from app.config import settings; print(settings.resolved_database_url)"
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
6. For `bale_enterprise` instances: test GRE flow, manual/catalog menus, live session routing, and SMS sync.
7. For `telegram_enterprise` instances: test dynamic route menus, manual/catalog menus, live session routing, and customizable button labels.
7. Review logs for errors/redaction correctness.

## Troubleshooting

- `Failed to decrypt config payload`:
  - `DATA_ENCRYPTION_KEY` changed after data was written.
- `database is locked`:
  - usually transient under SQLite; service retries runtime-state updates.
  - increase `SQLITE_BUSY_TIMEOUT_MS` or switch to PostgreSQL for high concurrency.
- `No module named psycopg2`:
  - install `requirements.txt` in the same virtualenv that runs `python -m uvicorn`.
- Chatwoot `404 Resource could not be found` for enterprise contacts/conversations:
  - the stored remote Chatwoot IDs are stale or deleted; the enterprise service will recreate the route session on the next forwardable customer message.
- Missing platform token errors:
  - ensure correct token key inside `platform_metadata`:
    - Bale / Bale Enterprise: `bale_token`
    - Telegram / Telegram Enterprise: `telegram_token`
- Webhooks not reaching the connector:
  - verify `SERVER_BASE_URL` is publicly reachable from Chatwoot.
  - check that the instance `webhook_url` is configured in the Chatwoot inbox settings.
- Enterprise SMS sync not fetching messages:
  - only applicable to `bale_enterprise`.
  - verify `ENTERPRISE_SMS_API_URL`, `ENTERPRISE_SMS_API_TOKEN`, and `ENTERPRISE_SMS_TOKEN_HEADER`.
  - check logs for HTTP timeouts or authentication errors.
- Telegram Enterprise dynamic routes not appearing:
  - verify `platform_metadata.enterprise_routes` is a non-empty array with valid `route_key` values.
  - check that `telegram_token` is set and the bot is reachable.
