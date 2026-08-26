# Refactor relocation ledger

This ledger is the audit trail for moving symbols during the object-oriented
reorganization. Keep one row for every baseline file or public symbol that is
renamed, split, wrapped, consolidated, or removed. Do not mark a row complete
until its behavior has coverage in the characterization or contract suite.

The baseline symbols and source locations come from:

```powershell
python scripts/inventory_baseline.py --write
```

## Status vocabulary

| Status | Meaning |
| --- | --- |
| `pending` | No relocation has been performed. |
| `moved` | The implementation has a new owner and behavior tests pass. |
| `delegated` | The old import/API delegates to the new owner for compatibility. |
| `consolidated` | Multiple equivalent implementations have one owner; equivalence is recorded. |
| `dead` | Proved unreachable/unused and intentionally removed; evidence is linked. |
| `blocked` | Relocation requires an unresolved external decision or behavior fixture. |

## Ledger

| Baseline symbol/file | Baseline location | New owner | Status | Verification/evidence |
| --- | --- | --- | --- | --- |
| *(populate from `baseline-inventory.json` before each move)* |  |  | `pending` |  |

## Completion rules

- Preserve public imports, route paths/methods/responses, settings names and
  defaults, database table/column metadata, platform keys, and frontend API
  request behavior unless an explicit compatibility shim is recorded.
- Record intentional consolidations and compatibility delegates rather than
  deleting the old row.
- The final comparison must have no `pending` rows and must include the test
  or contract that verifies every `moved`, `delegated`, `consolidated`, and
  `dead` row.
