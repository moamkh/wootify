"""
Module Overview
---------------
Purpose: Repository-layer data access helpers for persistence operations.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import MessageMapping


class MessageMappingRepository:
    """Repository for message mapping persistence operations."""
    def __init__(self, db: Session):
        """Initialize the instance."""
        self.db = db

    def list_by_conversation(self, conversation_id: str) -> list[MessageMapping]:
        """List by conversation."""
        return (
            self.db.query(MessageMapping)
            .filter(MessageMapping.conversation_id == str(conversation_id))
            .order_by(MessageMapping.created_at.desc())
            .all()
        )

    def get_by_chatwoot_message_id(self, conversation_id: str, chatwoot_message_id: str) -> Optional[MessageMapping]:
        """Get by chatwoot message id."""
        return (
            self.db.query(MessageMapping)
            .filter(
                MessageMapping.conversation_id == str(conversation_id),
                MessageMapping.chatwoot_message_id == str(chatwoot_message_id),
            )
            .one_or_none()
        )

    def get_by_platform_message_id(self, conversation_id: str, platform_message_id: str) -> Optional[MessageMapping]:
        """Get by platform message id."""
        return (
            self.db.query(MessageMapping)
            .filter(
                MessageMapping.conversation_id == str(conversation_id),
                MessageMapping.platform_message_id == str(platform_message_id),
            )
            .one_or_none()
        )

    def save(self, row: MessageMapping) -> MessageMapping:
        """Save."""
        self.db.add(row)
        self.db.flush()
        return row

