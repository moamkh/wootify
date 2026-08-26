"""Compatibility facade for the versioned API controller.

Handlers are implemented in the focused router package while route ownership
is exposed through focused routers.  Keeping this module's import path and
the shared webhook-task set stable protects bootstrap code and integrations
that imported the old controller directly.
"""

from __future__ import annotations

from fastapi import APIRouter

from wootify.presentation.http import api_v1_impl as _impl
from wootify.presentation.http.routers import (
    bale_pv_instagram as _bale_pv_instagram_router,
    enterprise as _enterprise_router,
    instances_platforms as _instances_platforms_router,
    mappings_system as _mappings_system_router,
    webhooks_simulation as _webhooks_simulation_router,
)

# Re-export the implementation module's public and private helpers for
# callers that historically imported endpoint functions from this module.
for _name in dir(_impl):
    if _name not in {"router", "__name__", "__package__", "__loader__", "__spec__"}:
        globals()[_name] = getattr(_impl, _name)

_webhook_delivery_tasks = _impl._webhook_delivery_tasks

router = APIRouter(prefix="/api/v1", tags=["api-v1"])

# Preserve the original declaration order.  This matters for overlapping
# parameterized paths and makes OpenAPI output stable across the refactor.
_routers = (
    _instances_platforms_router.router,
    _webhooks_simulation_router.router,
    _bale_pv_instagram_router.router,
    _enterprise_router.router,
    _mappings_system_router.router,
)
_routes_by_identity = {id(route): route for child in _routers for route in child.routes}
router.routes.extend(_routes_by_identity[id(route)] for route in _impl.router.routes if id(route) in _routes_by_identity)

if len(router.routes) != len(_impl.router.routes):
    raise RuntimeError("API route partition is incomplete; every legacy route must belong to one focused router")


def __getattr__(name: str):
    """Resolve implementation symbols added after this facade was written."""

    return getattr(_impl, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_impl)))


__all__ = [name for name in dir(_impl) if not name.startswith("__") and name != "router"] + ["router", "_webhook_delivery_tasks"]
