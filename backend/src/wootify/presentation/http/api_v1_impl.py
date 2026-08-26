"""Backward-compatible API implementation facade.

The handler/controller implementation lives under ``routers`` so the HTTP
implementation is physically colocated with its route areas.  This module
retains the historical import path for internal callers and integrations.
"""

from __future__ import annotations

from fastapi import APIRouter

from wootify.presentation.http.routers import _handler_controller
from wootify.presentation.http.routers import (
    bale_pv_instagram as _bale_pv_instagram_router,
    enterprise as _enterprise_router,
    instances_platforms as _instances_platforms_router,
    mappings_system as _mappings_system_router,
    webhooks_simulation as _webhooks_simulation_router,
)

for _name in dir(_handler_controller):
    if _name not in {"router", "__name__", "__package__", "__loader__", "__spec__"}:
        globals()[_name] = getattr(_handler_controller, _name)

_webhook_delivery_tasks = _handler_controller._webhook_delivery_tasks

router = APIRouter(prefix="/api/v1", tags=["api-v1"])
_routers = (
    _instances_platforms_router.router,
    _webhooks_simulation_router.router,
    _bale_pv_instagram_router.router,
    _enterprise_router.router,
    _mappings_system_router.router,
)
_routes_by_identity = {id(route): route for child in _routers for route in child.routes}
router.routes.extend(
    _routes_by_identity[id(route)]
    for route in _handler_controller.router.routes
    if id(route) in _routes_by_identity
)

if len(router.routes) != len(_handler_controller.router.routes):
    raise RuntimeError("API route partition is incomplete")


def __getattr__(name: str):
    return getattr(_handler_controller, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_handler_controller)))


__all__ = [
    name for name in dir(_handler_controller) if not name.startswith("__") and name != "router"
] + ["router", "_webhook_delivery_tasks"]
