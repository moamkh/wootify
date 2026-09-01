"""Public Chatwoot webhook and platform simulation endpoints."""

from fastapi import APIRouter

from ._selection import path_starts_with, select_routes

router = APIRouter()
router.routes.extend(select_routes(path_starts_with("/api/v1/webhooks/", "/api/v1/simulate/")))

__all__ = ["router"]
