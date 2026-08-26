"""Backward-compatible Wootify module entrypoint."""

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
