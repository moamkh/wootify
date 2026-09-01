# Repository briefing

This is a Python/React monorepo for a Chatwoot-to-messaging-platform bridge.
The backend is an installable `wootify` package, the admin UI is a Vite app,
and the Bale PV protocol implementation is an independent workspace package.

## Start here

- Backend factory: `backend/src/wootify/bootstrap/app.py`
- Composition root: `backend/src/wootify/bootstrap/container.py`
- HTTP presentation: `backend/src/wootify/presentation/http/`
- Messaging use cases: `backend/src/wootify/application/messaging/`
- Enterprise use cases: `backend/src/wootify/application/enterprise/`
- Platform implementations: `backend/src/wootify/plugins/`
- Persistence: `backend/src/wootify/infrastructure/persistence/`
- Frontend composition/features: `frontend/src/app/`, `frontend/src/features/`
- Tests: `backend/tests/`, `packages/bale-pv-client/tests/`

## Boundaries

- `domain` contains stable values and no web/database implementation details.
- `application` owns workflows and declares infrastructure ports.
- `plugins` own messaging-platform behavior.
- `infrastructure` owns persistence, security, clients, storage, and logging.
- `presentation` owns FastAPI contracts and authentication middleware.
- `bootstrap` is the only layer that composes concrete dependencies.

The `services`, `controllers`, `repositories`, `clients`, and `utils`
directories are compatibility facades. Add new implementation code to its
owning layer rather than those facades.

## Common commands

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
python -m uvicorn wootify.bootstrap.app:app --reload
cd frontend
npm run build
```

Use `alembic upgrade head` for schema upgrades. Runtime files belong under
`var/`; existing legacy locations continue to work. Preview an optional layout
migration with `python scripts/migrate_runtime_layout.py`.

Public HTTP routes, settings, database metadata, platform keys, old import
paths, and frontend request behavior are compatibility surfaces. Update tests
and `docs/refactor/relocation-ledger.md` whenever responsibility moves.
