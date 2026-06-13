"""
Module Overview
---------------
Purpose: Database engine/session initialization and request-scoped session helpers.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import logging
from typing import Any
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings

logger = logging.getLogger('app.db')

DATABASE_URL = settings.resolved_database_url


def _is_sqlite_url(url: str) -> bool:
    """Return whether the provided SQLAlchemy URL points to SQLite."""
    return str(url or '').strip().startswith('sqlite')


def _is_postgresql_url(url: str) -> bool:
    """Return whether the provided SQLAlchemy URL points to PostgreSQL."""
    return str(url or '').strip().startswith('postgresql')


def _connect_args(url: str) -> dict[str, Any]:
    """Build SQLAlchemy connect args for the active database backend."""
    if _is_sqlite_url(url):
        timeout_seconds = max(float(settings.SQLITE_BUSY_TIMEOUT_MS) / 1000.0, 1.0)
        return {"check_same_thread": False, "timeout": timeout_seconds}
    return {}


def _quote_postgresql_identifier(value: str) -> str:
    """Quote a PostgreSQL identifier safely for CREATE DATABASE."""
    return f'"{str(value or "").replace(chr(34), chr(34) * 2)}"'


def ensure_database_exists() -> None:
    """Create the configured PostgreSQL database if it does not already exist."""
    if not settings.DATABASE_AUTO_CREATE or not _is_postgresql_url(DATABASE_URL):
        return

    admin_url = settings.postgres_admin_url
    if not admin_url:
        raise ValueError('postgres_admin_url could not be resolved')

    target_database_url = settings.resolved_database_url
    db_name = str(make_url(target_database_url).database or '').strip()
    if not db_name:
        raise ValueError('database name could not be resolved from DATABASE_URL')

    # Connect to an existing admin database first; the target database may not
    # exist yet, so the main engine cannot be created safely at this point.
    admin_engine = create_engine(
        admin_url,
        isolation_level='AUTOCOMMIT',
        pool_pre_ping=True,
    )
    try:
        with admin_engine.connect() as connection:
            exists = connection.execute(
                text('SELECT 1 FROM pg_database WHERE datname = :name'),
                {'name': db_name},
            ).scalar()
            if exists:
                return
            connection.exec_driver_sql(
                f'CREATE DATABASE {_quote_postgresql_identifier(db_name)}'
            )
            logger.info('created postgresql database name=%s', db_name)
    finally:
        admin_engine.dispose()


ensure_database_exists()

def _engine_kwargs(url: str) -> dict[str, Any]:
    """Build extra engine kwargs for the active database backend."""
    kwargs: dict[str, Any] = {"connect_args": _connect_args(url)}
    if _is_sqlite_url(url):
        # StaticPool reuses a single connection for SQLite, reducing
        # connection churn and lock contention in WAL mode.
        kwargs["poolclass"] = StaticPool
        kwargs["pool_pre_ping"] = False
    else:
        kwargs["pool_pre_ping"] = True
    return kwargs


engine = create_engine(DATABASE_URL, **_engine_kwargs(DATABASE_URL))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


if _is_sqlite_url(DATABASE_URL):

    @event.listens_for(engine, 'connect')
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute(f'PRAGMA journal_mode={settings.SQLITE_JOURNAL_MODE};')
        cursor.execute(f'PRAGMA busy_timeout={int(settings.SQLITE_BUSY_TIMEOUT_MS)};')
        cursor.execute('PRAGMA synchronous=NORMAL;')
        cursor.execute('PRAGMA foreign_keys=ON;')
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    """Get db."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        try:
            db.rollback()
        except Exception:
            logger.exception('database rollback failed')
        raise
    finally:
        try:
            db.close()
        except Exception:
            logger.exception('database session close failed')

