"""
Module Overview
---------------
Purpose: Repository-layer data access helpers for persistence operations.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import ConversationRuntimeState


class ConversationRuntimeStateRepository:
    """Repository for conversation runtime state persistence operations."""
    def __init__(self, db: Session):
        """Initialize the instance."""
        self.db = db

    def get(self, conversation_id: str) -> Optional[ConversationRuntimeState]:
        """Get conversation runtime state by conversation id."""
        return self.db.get(ConversationRuntimeState, str(conversation_id))

    def get_or_create(self, conversation_id: str) -> ConversationRuntimeState:
        """Get or create."""
        row = self.get(conversation_id)
        if row:
            return row
        row = ConversationRuntimeState(conversation_id=str(conversation_id))
        self.db.add(row)
        self.db.flush()
        return row

    def save(self, row: ConversationRuntimeState) -> ConversationRuntimeState:
        """Persist conversation runtime state changes."""
        self.db.add(row)
        return row

