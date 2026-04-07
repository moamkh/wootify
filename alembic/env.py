"""
Module Overview
---------------
Purpose: Alembic runtime environment configuration for online/offline migrations.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import os
import sys
import logging
from pathlib import Path
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

from app.models import Base  # noqa: E402

config = context.config
logger = logging.getLogger('app.alembic')

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    """Get url."""
    try:
        env_url = os.getenv("DATABASE_URL")
        if env_url:
            return env_url

        url = config.get_main_option("sqlalchemy.url")
        if url.startswith("sqlite:///./"):
            return f"sqlite:///{(repo_root / url.removeprefix('sqlite:///./')).as_posix()}"
        return url
    except Exception:
        logger.exception('failed to resolve alembic database url')
        raise


def run_migrations_offline() -> None:
    """Run migrations offline."""
    try:
        url = _get_url()
        context.configure(
            url=url,
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()
    except Exception:
        logger.exception('alembic offline migration failed')
        raise


def run_migrations_online() -> None:
    """Run migrations online."""
    try:
        configuration = config.get_section(config.config_ini_section) or {}
        configuration["sqlalchemy.url"] = _get_url()

        connectable = engine_from_config(
            configuration,
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

        with connectable.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

            with context.begin_transaction():
                context.run_migrations()
    except Exception:
        logger.exception('alembic online migration failed')
        raise


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

