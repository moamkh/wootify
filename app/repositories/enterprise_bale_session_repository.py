"""
Module Overview
---------------
Purpose: Repository-layer data access helpers for enterprise Bale sessions.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session, selectinload

from app.models import EnterpriseBaleSession, EnterpriseBaleUser, EnterpriseSessionStatus


class EnterpriseBaleSessionRepository:
    """Repository for enterprise Bale session persistence operations."""

    def __init__(self, db: Session):
        """Initialize the instance."""
        self.db = db

    def get_by_id(self, session_id: str) -> Optional[EnterpriseBaleSession]:
        """Get a session by id."""
        return self.db.get(EnterpriseBaleSession, str(session_id))

    def get_latest_for_user_route(self, user_id: str, route_key: str) -> Optional[EnterpriseBaleSession]:
        """Get the latest session for a user and route."""
        return (
            self.db.query(EnterpriseBaleSession)
            .filter(
                EnterpriseBaleSession.user_id == str(user_id),
                EnterpriseBaleSession.route_key == str(route_key),
            )
            .order_by(EnterpriseBaleSession.updated_at.desc(), EnterpriseBaleSession.created_at.desc())
            .first()
        )

    def get_unresolved_for_user_route(self, user_id: str, route_key: str) -> Optional[EnterpriseBaleSession]:
        """Get the latest unresolved session for a user and route."""
        return (
            self.db.query(EnterpriseBaleSession)
            .filter(
                EnterpriseBaleSession.user_id == str(user_id),
                EnterpriseBaleSession.route_key == str(route_key),
                EnterpriseBaleSession.status != EnterpriseSessionStatus.resolved,
            )
            .order_by(EnterpriseBaleSession.updated_at.desc(), EnterpriseBaleSession.created_at.desc())
            .first()
        )

    def get_by_chatwoot_conversation_id(
        self,
        instance_id: str,
        chatwoot_conversation_id: str,
    ) -> Optional[EnterpriseBaleSession]:
        """Get a session by instance and Chatwoot conversation id."""
        return (
            self.db.query(EnterpriseBaleSession)
            .join(EnterpriseBaleSession.user)
            .filter(
                EnterpriseBaleSession.chatwoot_conversation_id == str(chatwoot_conversation_id),
                EnterpriseBaleUser.instance_id == str(instance_id),
            )
            .order_by(EnterpriseBaleSession.updated_at.desc())
            .first()
        )

    def list_by_instance(self, instance_id: str) -> list[EnterpriseBaleSession]:
        """List sessions for an instance with eagerly loaded user and pending messages."""
        return (
            self.db.query(EnterpriseBaleSession)
            .join(EnterpriseBaleSession.user)
            .filter(EnterpriseBaleUser.instance_id == str(instance_id))
            .options(
                selectinload(EnterpriseBaleSession.user),
                selectinload(EnterpriseBaleSession.pending_messages),
            )
            .order_by(EnterpriseBaleSession.updated_at.desc(), EnterpriseBaleSession.created_at.desc())
            .all()
        )

    def save(self, row: EnterpriseBaleSession) -> EnterpriseBaleSession:
        """Persist a session row."""
        self.db.add(row)
        self.db.flush()
        return row
