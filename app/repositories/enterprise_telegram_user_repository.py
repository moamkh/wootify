"""
Module Overview
---------------
Purpose: Repository-layer data access helpers for enterprise Telegram users.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import EnterpriseTelegramUser


class EnterpriseTelegramUserRepository:
    """Repository for enterprise Telegram user persistence operations."""

    def __init__(self, db: Session):
        """Initialize the instance."""
        self.db = db

    def get_by_id(self, user_id: str) -> Optional[EnterpriseTelegramUser]:
        """Get a user by id."""
        return self.db.get(EnterpriseTelegramUser, str(user_id))

    def get_by_platform_chat_id(self, instance_id: str, platform_chat_id: str) -> Optional[EnterpriseTelegramUser]:
        """Get a user by instance and Telegram chat id."""
        return (
            self.db.query(EnterpriseTelegramUser)
            .filter(
                EnterpriseTelegramUser.instance_id == str(instance_id),
                EnterpriseTelegramUser.platform_chat_id == str(platform_chat_id),
            )
            .one_or_none()
        )

    def list_by_instance(self, instance_id: str) -> list[EnterpriseTelegramUser]:
        """List users for an instance."""
        return (
            self.db.query(EnterpriseTelegramUser)
            .filter(EnterpriseTelegramUser.instance_id == str(instance_id))
            .order_by(EnterpriseTelegramUser.updated_at.desc())
            .all()
        )

    def save(self, row: EnterpriseTelegramUser) -> EnterpriseTelegramUser:
        """Persist a user row."""
        self.db.add(row)
        self.db.flush()
        return row
