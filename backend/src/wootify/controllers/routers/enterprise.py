"""Enterprise asset, manual-group, routing, and operations endpoints."""

from fastapi import APIRouter

from ._selection import path_starts_with, select_routes

router = APIRouter()
router.routes.extend(select_routes(path_starts_with("/api/v1/instances/{instance_key}/enterprise/")))

__all__ = ["router"]
