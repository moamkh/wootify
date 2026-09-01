# Comparison with stable v5.0.1

Comparison target: tag `v5.0.1` (commit `9f76d08`). The refactor branch was
checked against that source and its tests; no runtime database migration or
runtime-data move was performed.

## Results

| Contract | v5.0.1 | Refactor | Result |
| --- | ---: | ---: | --- |
| Python tests | 203 passed | 229 passed | PASS |
| FastAPI method/path pairs | 56 | 56 | Exact match |
| SQLAlchemy tables | 20 | 20 | Exact names/columns |
| Settings classes | 1 | 1 | Baseline fields preserved |
| Platform registry signals | 6 | 6 | Exact key set |
| Frontend API exports | 1 module | 13 focused modules | Export set preserved |

The 26 additional current tests cover the application factory, unit of work,
runtime-layout migration, plugin registration, extracted messaging/enterprise
objects, and route contracts. The static audit accounts for all 1,370 stable
functions, classes, and methods and all stable frontend API exports. The only
intentional object-oriented relocation is the module-level `lifespan` callable
becoming `ApplicationLifecycle.lifespan`, with the legacy alias retained.

## Intentional differences

- Implementation files moved into `backend/src/wootify`, `frontend`, and
  `packages/bale-pv-client`; historical import paths remain compatibility
  facades.
- New installations use `var/`/`WOOTIFY_VAR_DIR` for generated runtime files.
  Existing root/data paths still win, and migration is explicit and dry-run
  first.
- `INSTAGRAM_PV_SESSION_DIR` and `LOG_FILE_PATH` now derive from the runtime
  layout while preserving existing deployments through path fallback.
- The frontend Yarn lock was removed in favor of the existing npm lockfile.

## Conclusion

Based on the stable test suite, exact route contract, ORM column contract,
settings/platform inventory, import compatibility checks, and symbol audit, no
behavioral regression was found relative to v5.0.1. External integrations still
need environment-specific end-to-end verification with real Chatwoot/platform
credentials.
