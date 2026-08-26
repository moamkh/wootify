"""Backward-compatible Wootify module entrypoint."""

from wootify.bootstrap.app import app, create_app, lifespan

__all__ = ["app", "create_app", "lifespan"]
