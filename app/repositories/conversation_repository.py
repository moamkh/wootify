"""
Module Overview
---------------
Purpose: Repository-layer data access helpers for persistence operations.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session, selectinload

from app.models import Conversation


class ConversationRepository:
    """Repository for conversation persistence operations."""
    def __init__(self, db: Session):
        """Initialize the instance."""
        self.db = db

    def list_by_instance(self, instance_id: str) -> list[Conversation]:
        """List by instance with eagerly loaded relationships."""
        return (
            self.db.query(Conversation)
            .filter(Conversation.instance_id == str(instance_id))
            .options(
                selectinload(Conversation.message_mappings),
                selectinload(Conversation.runtime_state),
            )
            .order_by(Conversation.is_active.desc(), Conversation.updated_at.desc())
            .all()
        )

    def get_by_id(self, conversation_id: str) -> Optional[Conversation]:
        """Get by id."""
        return self.db.get(Conversation, str(conversation_id))

    def get_by_platform_id(self, instance_id: str, platform_conversation_id: str) -> Optional[Conversation]:
        """Get by platform id."""
        return (
            self.db.query(Conversation)
            .filter(
                Conversation.instance_id == str(instance_id),
                Conversation.platform_conversation_id == str(platform_conversation_id),
                Conversation.is_active.is_(True),
            )
            .order_by(Conversation.updated_at.desc())
            .first()
        )

    def get_by_chatwoot_id(self, instance_id: str, chatwoot_conversation_id: str) -> Optional[Conversation]:
        """Get by chatwoot id."""
        return (
            self.db.query(Conversation)
            .filter(
                Conversation.instance_id == str(instance_id),
                Conversation.chatwoot_conversation_id == str(chatwoot_conversation_id),
            )
            .one_or_none()
        )

    def list_by_contact(
        self,
        instance_id: str,
        chatwoot_contact_id: str,
        chatwoot_inbox_id: Optional[str] = None,
    ) -> list[Conversation]:
        """List by contact."""
        query = self.db.query(Conversation).filter(
            Conversation.instance_id == str(instance_id),
            Conversation.chatwoot_contact_id == str(chatwoot_contact_id),
        )
        if chatwoot_inbox_id is not None:
            query = query.filter(Conversation.chatwoot_inbox_id == str(chatwoot_inbox_id))
        return query.order_by(Conversation.is_active.desc(), Conversation.updated_at.desc()).all()

    def deactivate_platform_mappings(
        self,
        instance_id: str,
        platform_conversation_id: str,
        *,
        exclude_conversation_id: Optional[str] = None,
    ) -> None:
        """Mark every other mapping for the same platform conversation as inactive."""
        query = self.db.query(Conversation).filter(
            Conversation.instance_id == str(instance_id),
            Conversation.platform_conversation_id == str(platform_conversation_id),
            Conversation.is_active.is_(True),
        )
        if exclude_conversation_id:
            query = query.filter(Conversation.id != str(exclude_conversation_id))
        query.update({Conversation.is_active: False}, synchronize_session=False)

    def save(self, row: Conversation) -> Conversation:
        """Save."""
        self.db.add(row)
        self.db.flush()
        return row

