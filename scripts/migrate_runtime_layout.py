"""Preview or apply the legacy-runtime-data migration into ``var``."""

from __future__ import annotations

import argparse

from wootify.infrastructure.storage.runtime_layout import RuntimeLayoutMigrator
from wootify.paths import PROJECT_ROOT, VAR_ROOT


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Move files. Without this flag the command is a read-only preview.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    migrator = RuntimeLayoutMigrator(PROJECT_ROOT, VAR_ROOT)
    items = migrator.plan()
    action = "MOVE" if args.apply else "WOULD MOVE"
    for item in items:
        print(f"{action}: {item.source} -> {item.destination}")
    if not items:
        print("No legacy runtime paths found.")
        return 0
    if args.apply:
        completed = migrator.apply(items)
        print(f"Migrated {len(completed)} runtime paths.")
    else:
        print("Dry run only. Re-run with --apply after stopping Wootify.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
