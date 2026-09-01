# Architecture

Wootify Connector is a modular FastAPI application that synchronizes Chatwoot
with Bale, Telegram, Bale PV, and an isolated experimental Instagram adapter.
The refactor keeps the existing HTTP, database, environment, and import
contracts while giving each concern a clear owner.

## Repository layout

```text
backend/
  src/wootify/
    bootstrap/        application factory, container, and lifecycle
    domain/           platform identities and capabilities
    application/      use cases, workflows, policies, and ports
    plugins/          platform-specific connectors and adapters
    infrastructure/   persistence, security, networking, storage, observability
    presentation/     FastAPI schemas, middleware, routers, and controllers
    services/         compatibility modules for historical imports
  migrations/         Alembic migrations
  tests/              behavior and contract tests
frontend/
  src/app/             application composition
  src/features/        feature-owned React views and workflows
  src/shared/          API transport and reusable UI
packages/bale-pv-client/ independently installable protocol client
scripts/               operational and refactor-audit commands
var/                   ignored runtime state for new installations
```

Historical modules under `wootify.services`, `wootify.controllers`,
`wootify.repositories`, `wootify.clients`, and `wootify.utils` remain thin
aliases. They preserve imports and monkeypatch behavior while the
implementation lives in its new package.

## Dependency direction

```text
presentation -> application -> domain
plugins ------> application -> domain
infrastructure implements application ports
bootstrap composes all of the above
```

The application layer does not construct FastAPI or SQLAlchemy infrastructure.
`ApplicationContainer` owns construction, and `SqlAlchemyUnitOfWork` implements
the transaction boundary declared by the application port.

## Runtime composition

`wootify.bootstrap.app.create_app()` creates the FastAPI application. It mounts
the API routers and UI, installs middleware and exception handlers, and stores
the dependency container on `app.state.container`. `ApplicationLifecycle`
coordinates database initialization, registry seeding, and polling startup and
shutdown. The module-level `app` and legacy `app.main:app` entrypoint are kept
for existing deployments.

The plugin registry describes each platform with a stable key, capabilities,
and connector factory. Built-ins retain the six existing platform keys.
Instagram is registered as experimental so it remains available without
leaking its implementation into the core application.

## Messaging flow

Inbound platform messages are polled by the platform plugin, normalized, and
passed to `BridgeService`. Dedicated parser, destination, media, notification,
and Bale PV workflow objects handle detailed policies. The workflow finds or
creates a Chatwoot contact/conversation and records conversation/message maps.

Outbound Chatwoot events enter through
`/api/v1/webhooks/chatwoot/{instance_key}`. The HTTP controller validates the
event, application services resolve the mapped destination, and the selected
plugin delivers text or media. Mapping records preserve reply threading and
idempotency.

Enterprise Bale and Telegram orchestration lives under
`application/enterprise`. Shared policy objects own route lookup, labels,
session transitions, and Chatwoot payload interpretation. Bale retains GRE and
optional SMS behavior; Telegram retains dynamic routes without GRE/SMS.

## HTTP presentation

`presentation/http/routers` partitions the existing API into instance/platform,
webhook/simulation, Bale PV/Instagram, enterprise, and mapping/system surfaces.
`HttpApplicationServices` centralizes service construction. The combined router
retains the original route declaration order, paths, methods, and handler
behavior. Contract tests compare all 53 API routes.

## Persistence

SQLAlchemy ownership is under `infrastructure/persistence`:

- `session.py` configures engines and sessions.
- `models/` splits the 20 existing tables by responsibility.
- `repositories/` contains persistence implementations.
- `unit_of_work.py` provides commit/rollback transaction semantics.

The `wootify.models` and `wootify.db` facades remain compatible. Alembic reads
migrations from `backend/migrations`; schema names, columns, indexes,
constraints, and revision history are unchanged.

## Runtime paths and migration

New installations place generated databases, logs, sessions, uploads, and
temporary files below `var/` (or `WOOTIFY_VAR_DIR`). Existing root/data paths
win when present, preserving deployed installations. Runtime data is never
moved implicitly. `python scripts/migrate_runtime_layout.py` previews a
collision-safe migration; `--apply` performs only displayed non-conflicting
moves.

## Compatibility and verification

The refactor is guarded by characterization tests, the exact API route
contract, application-factory tests, persistence tests, frontend production
builds, and before/after static inventories. Baseline snapshots and symbol
relocation evidence live in `docs/refactor/`.
