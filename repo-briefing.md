# Repo Briefing: `eaita_chatwoot_connector`

## What this report is
A fast, source-grounded starting point. Verify important claims by opening the referenced files.

## High-signal files/folders to read
- `README.md`
- `docs`
- `CONTRIBUTING.md`
- `.env.example`

## Tech signals
- Languages (by file extension sample): Python (43), HTML (2), JSON (2), JavaScript (2), React (JSX) (2), CSS (1)
- Dependency/build manifests:
  - `requirements.txt`
  - `wootify-instance-manager/package.json`
  - `wootify-instance-manager/yarn.lock`
- Frameworks / runtimes (heuristic):
  - FastAPI (from `requirements.txt`)
  - SQLAlchemy (from `requirements.txt`)
  - React (from `wootify-instance-manager/package.json`)

## Top-level layout
- Directories:
  - `.github/`
  - `alembic/`
  - `app/`
  - `docs/`
  - `wootify-instance-manager/`
- Files:
  - `.env`
  - `.env.example`
  - `.gitignore`
  - `alembic.ini`
  - `backend.err.log`
  - `backend.log`
  - `backend.run.err`
  - `backend.run.log`
  - `CODE_OF_CONDUCT.md`
  - `CONTRIBUTING.md`
  - `frontend.run.err`
  - `frontend.run.log`
  - `LICENSE`
  - `README.md`
  - `requirements.txt`
  - `SECURITY.md`
  - `wootify.db`
  - `wootify.db-shm`
  - `wootify.db-wal`

## Likely entrypoints
- `app/main.py`

## Next steps (manual, higher confidence)
- Open `README*` / `docs/` and write a 5–10 line purpose summary.
- Identify how to run locally (commands, env vars), and record them verbatim.
- Trace one main flow end-to-end (API request or job): entry → handler → service → DB/integrations.
- List key dependencies/integrations (DB, cache, queue, external APIs) with evidence paths.
