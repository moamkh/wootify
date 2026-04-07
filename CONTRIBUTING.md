# Contributing Guide

Thanks for contributing to Wootify Connector.

## Before You Start

- Read `README.md` for setup.
- Read `CODE_OF_CONDUCT.md`.
- Check existing issues/PRs before starting large changes.

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Optional frontend:

```bash
cd wootify-instance-manager
npm install
npm run dev
```

## Branch and Commit Conventions

- Create feature branches from `main`.
- Keep commits focused and small.
- Prefer conventional commit style when possible:
  - `feat: ...`
  - `fix: ...`
  - `docs: ...`
  - `refactor: ...`
  - `test: ...`
  - `chore: ...`

## Code Standards

- Python code should keep behavior-focused, concise docstrings.
- Follow documentation standards in `docs/COMMENTING_STANDARD.md`.
- Keep comments/docstrings in English.
- Avoid mixing refactors with functional changes in one PR.

## Pull Request Checklist

- [ ] Change is scoped and described clearly.
- [ ] README/docs updated when behavior or config changes.
- [ ] Backward compatibility considered (API and DB).
- [ ] Migrations included when schema changes are introduced.
- [ ] Manual test steps included in PR description.

## Areas Where Help Is Valuable

- Automated test coverage (unit + integration).
- Connector reliability and retry strategies.
- Observability improvements (structured logs, metrics).
- Documentation and onboarding material.

