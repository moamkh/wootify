"""Legacy ``app.main:app`` entrypoint.

The implementation lives in :mod:`wootify.bootstrap.app`.  This module is
kept intentionally small so existing Uvicorn commands remain valid.
"""

from wootify.bootstrap.app import (
    app,
    create_app,
    instagram_polling_service,
    lifespan,
    polling_service,
)

__all__ = [
    "app",
    "create_app",
    "lifespan",
    "polling_service",
    "instagram_polling_service",
]
