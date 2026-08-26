"""Conversation/message mapping and system metadata endpoints."""

from fastapi import APIRouter

from ._selection import endpoint_named, select_routes

router = APIRouter()
router.routes.extend(
    select_routes(
        endpoint_named(
            "list_instance_conversations",
            "get_instance_conversation",
            "list_conversation_messages",
            "get_version",
        )
    )
)

__all__ = ["router"]
