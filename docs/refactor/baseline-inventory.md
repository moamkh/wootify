# Baseline inventory

This document is the human-readable baseline for the behavior-preserving
refactor. The machine-readable snapshot was generated from baseline commit
`91d77c0`, before any structural moves, so it can be compared with the
completed branch:

```powershell
python scripts/inventory_baseline.py --repo-root <baseline-checkout> --output <current-checkout>/docs/refactor/baseline-inventory.json
```

That command writes `docs/refactor/baseline-inventory.json` alongside this
summary. It performs static AST/source inspection and does not import the
application, open the database, load `.env`, or touch `data/`.

## Current snapshot

The baseline snapshot contains:

| Surface | Count |
| --- | ---: |
| Python source modules | 85 |
| FastAPI routes (including `/api/v1` router prefix) | 56 |
| `BaseSettings` classes | 1 |
| SQLAlchemy ORM tables | 20 |
| platform registry key signals | 6 |
| frontend files exporting API functions | 1 |

The JSON records each module, top-level function, class and method with source
location; each route with method/path/handler; settings fields and defaults;
ORM table/column declarations; platform registry references; and exported
frontend API function names and line numbers. Counts above are informational;
the JSON snapshot is the comparison input for the final audit. A separate final
inventory records the refactored tree; compatibility wrappers and the
relocation ledger explain symbols whose qualified owner changed.

## Scope and exclusions

The inventory scans `backend/src/wootify` (or legacy `app`), the Bale PV client
source package, and `frontend` (or legacy `wootify-instance-manager`). Tests,
build output, virtual environments, logs, databases, uploads, and session
files are excluded from the source inventory.
