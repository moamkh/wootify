"""
Module Overview
---------------
Purpose: Repository-layer data access helpers for enterprise Telegram sessions.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session, selectinload

from app.models import EnterpriseTelegramSession, EnterpriseTelegramUser, EnterpriseSessionStatus


class EnterpriseTelegramSessionRepository:
    """Repository for enterprise Telegram session persistence operations."""

    def __init__(self, db: Session):
        """Initialize the instance."""
        self.db = db

    def get_by_id(self, session_id: str) -> Optional[EnterpriseTelegramSession]:
        """Get a session by id."""
        return self.db.get(EnterpriseTelegramSession, str(session_id))

    def get_latest_for_user_route(self, user_id: str, route_key: str) -> Optional[EnterpriseTelegramSession]:
        """Get the latest session for a user and route."""
        return (
            self.db.query(EnterpriseTelegramSession)
            .filter(
                EnterpriseTelegramSession.user_id == str(user_id),
                EnterpriseTelegramSession.route_key == str(route_key),
            )
            .order_by(EnterpriseTelegramSession.updated_at.desc(), EnterpriseTelegramSession.created_at.desc())
            .first()
        )

    def get_unresolved_for_user_route(self, user_id: str, route_key: str) -> Optional[EnterpriseTelegramSession]:
        """Get the latest unresolved session for a user and route."""
        return (
            self.db.query(EnterpriseTelegramSession)
            .filter(
                EnterpriseTelegramSession.user_id == str(user_id),
                EnterpriseTelegramSession.route_key == str(route_key),
                EnterpriseTelegramSession.status != EnterpriseSessionStatus.resolved,
            )
            .order_by(EnterpriseTelegramSession.updated_at.desc(), EnterpriseTelegramSession.created_at.desc())
            .first()
        )

    def get_by_chatwoot_conversation_id(
        self,
        instance_id: str,
        chatwoot_conversation_id: str,
    ) -> Optional[EnterpriseTelegramSession]:
        """Get a session by instance and Chatwoot conversation id."""
        return (
            self.db.query(EnterpriseTelegramSession)
            .join(EnterpriseTelegramSession.user)
            .filter(
                EnterpriseTelegramSession.chatwoot_conversation_id == str(chatwoot_conversation_id),
                EnterpriseTelegramUser.instance_id == str(instance_id),
            )
            .order_by(EnterpriseTelegramSession.updated_at.desc())
            .first()
        )

    def list_by_instance(self, instance_id: str) -> list[EnterpriseTelegramSession]:
        """List sessions for an instance with eagerly loaded user and pending messages."""
        return (
            self.db.query(EnterpriseTelegramSession)
            .join(EnterpriseTelegramSession.user)
            .filter(EnterpriseTelegramUser.instance_id == str(instance_id))
            .options(
                selectinload(EnterpriseTelegramSession.user),
                selectinload(EnterpriseTelegramSession.pending_messages),
            )
            .order_by(EnterpriseTelegramSession.updated_at.desc(), EnterpriseTelegramSession.created_at.desc())
            .all()
        )

    def save(self, row: EnterpriseTelegramSession) -> EnterpriseTelegramSession:
        """Persist a session row."""
        self.db.add(row)
        self.db.flush()
        return row
