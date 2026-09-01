"""Platform discovery and connector-instance management endpoints."""

from fastapi import APIRouter

from ._selection import select_routes

_INSTANCE_ENDPOINTS = {
    "list_platform_types",
    "list_features",
    "list_instances",
    "create_instance",
    "get_instance",
    "instance_health",
    "patch_instance",
    "delete_instance",
    "create_chatwoot_inbox",
    "get_chatwoot_webhook",
    "configure_chatwoot_webhook",
}

router = APIRouter()
router.routes.extend(select_routes(lambda route: route.name in _INSTANCE_ENDPOINTS))

__all__ = ["router"]
