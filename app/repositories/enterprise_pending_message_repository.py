"""
Module Overview
---------------
Purpose: Repository-layer data access helpers for enterprise queued operator messages.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import EnterprisePendingMessage, EnterprisePendingMessageStatus


class EnterprisePendingMessageRepository:
    """Repository for enterprise pending message persistence operations."""

    def __init__(self, db: Session):
        """Initialize the instance."""
        self.db = db

    def get_by_chatwoot_message_id(self, session_id: str, chatwoot_message_id: str) -> Optional[EnterprisePendingMessage]:
        """Get a pending row by Chatwoot message id."""
        return (
            self.db.query(EnterprisePendingMessage)
            .filter(
                EnterprisePendingMessage.session_id == str(session_id),
                EnterprisePendingMessage.chatwoot_message_id == str(chatwoot_message_id),
            )
            .one_or_none()
        )

    def list_pending_for_session(self, session_id: str) -> list[EnterprisePendingMessage]:
        """List pending messages for a session in FIFO order."""
        return (
            self.db.query(EnterprisePendingMessage)
            .filter(
                EnterprisePendingMessage.session_id == str(session_id),
                EnterprisePendingMessage.status == EnterprisePendingMessageStatus.pending,
            )
            .order_by(EnterprisePendingMessage.created_at.asc())
            .all()
        )

    def save(self, row: EnterprisePendingMessage) -> EnterprisePendingMessage:
        """Persist a pending message row."""
        self.db.add(row)
        self.db.flush()
        return row
