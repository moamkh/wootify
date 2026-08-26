"""Bale PV and Instagram PV operation endpoints."""

from fastapi import APIRouter

from ._selection import path_starts_with, select_routes

router = APIRouter()
router.routes.extend(select_routes(path_starts_with("/api/v1/instances/{instance_key}/bale-pv/", "/api/v1/instances/{instance_key}/instagram-pv/")))

__all__ = ["router"]
