"""Application composition and process lifecycle."""

import importlib

__all__ = ["app", "create_app", "ApplicationContainer"]


def __getattr__(name: str):
    """Load the process app lazily so architectural ports stay importable."""
    if name in {"app", "create_app", "lifespan"}:
        app_module = importlib.import_module("wootify.bootstrap.app")
        return getattr(app_module, name)
    if name == "ApplicationContainer":
        from wootify.bootstrap.container import ApplicationContainer

        return ApplicationContainer
    raise AttributeError(name)
