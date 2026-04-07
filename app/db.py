"""
Module Overview
---------------
Purpose: Database engine/session initialization and request-scoped session helpers.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import logging
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

logger = logging.getLogger('app.db')


def _connect_args():
    """Connect args."""
    if settings.DATABASE_URL.startswith("sqlite"):
        timeout_seconds = max(float(settings.SQLITE_BUSY_TIMEOUT_MS) / 1000.0, 1.0)
        return {"check_same_thread": False, "timeout": timeout_seconds}
    return {}


engine = create_engine(settings.DATABASE_URL, connect_args=_connect_args(), pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


if settings.DATABASE_URL.startswith('sqlite'):

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

