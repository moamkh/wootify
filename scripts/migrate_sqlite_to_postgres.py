"""
One-time data migration helper from the local SQLite database into PostgreSQL.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

from app.config import settings  # noqa: E402
from app.db import DATABASE_URL, ensure_database_exists  # noqa: E402
from app.models import Base  # noqa: E402


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description='Migrate application data from SQLite to PostgreSQL.',
    )
    parser.add_argument(
        '--source-url',
        default=settings.sqlite_migration_source_url,
        help='Source SQLite SQLAlchemy URL.',
    )
    parser.add_argument(
        '--target-url',
        default=DATABASE_URL,
        help='Target PostgreSQL SQLAlchemy URL.',
    )
    parser.add_argument(
        '--chunk-size',
        type=int,
        default=500,
        help='Number of rows to insert per batch.',
    )
    parser.add_argument(
        '--allow-nonempty-target',
        action='store_true',
        help='Allow migration into a target database that already contains rows.',
    )
    return parser.parse_args()


def _target_has_rows(connection) -> bool:
    """Return whether any application table already contains data."""
    for table in Base.metadata.sorted_tables:
        if connection.execute(select(table).limit(1)).first() is not None:
            return True
    return False


def _copy_table_rows(source_connection, target_connection, *, chunk_size: int) -> list[tuple[str, int]]:
    """Copy all application tables from SQLite into PostgreSQL."""
    source_tables = set(inspect(source_connection).get_table_names())
    copied: list[tuple[str, int]] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in source_tables:
            continue

        total = 0
        result = source_connection.execute(select(table))
        while True:
            batch = result.mappings().fetchmany(max(1, int(chunk_size)))
            if not batch:
                break
            payload = [dict(row) for row in batch]
            target_connection.execute(table.insert(), payload)
            total += len(payload)
        copied.append((table.name, total))

    return copied


def _stamp_target_database(target_url: str) -> None:
    """Mark the migrated target database as being at the current Alembic head."""
    alembic_config = Config(str(repo_root / 'alembic.ini'))
    alembic_config.set_main_option('sqlalchemy.url', target_url)
    command.stamp(alembic_config, 'head')


def main() -> int:
    """Run the migration and print per-table copy counts."""
    args = _parse_args()
    ensure_database_exists()

    source_engine = create_engine(args.source_url, pool_pre_ping=True)
    target_engine = create_engine(args.target_url, pool_pre_ping=True)

    try:
        Base.metadata.create_all(bind=target_engine)

        with source_engine.connect() as source_connection:
            with target_engine.begin() as target_connection:
                if not args.allow_nonempty_target and _target_has_rows(target_connection):
                    raise RuntimeError(
                        'target database already contains application rows; refusing to overwrite it',
                    )

                copied = _copy_table_rows(
                    source_connection,
                    target_connection,
                    chunk_size=args.chunk_size,
                )

        _stamp_target_database(args.target_url)

        print(f'source={args.source_url}')
        print(f'target={args.target_url}')
        for table_name, row_count in copied:
            print(f'{table_name}: {row_count}')
        return 0
    finally:
        source_engine.dispose()
        target_engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
