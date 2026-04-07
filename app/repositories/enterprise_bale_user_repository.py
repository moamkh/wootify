"""
Module Overview
---------------
Purpose: Repository-layer data access helpers for enterprise Bale users.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import EnterpriseBaleUser


class EnterpriseBaleUserRepository:
    """Repository for enterprise Bale user persistence operations."""

    def __init__(self, db: Session):
        """Initialize the instance."""
        self.db = db

    def get_by_id(self, user_id: str) -> Optional[EnterpriseBaleUser]:
        """Get a user by id."""
        return self.db.get(EnterpriseBaleUser, str(user_id))

    def get_by_platform_chat_id(self, instance_id: str, platform_chat_id: str) -> Optional[EnterpriseBaleUser]:
        """Get a user by instance and Bale chat id."""
        return (
            self.db.query(EnterpriseBaleUser)
            .filter(
                EnterpriseBaleUser.instance_id == str(instance_id),
                EnterpriseBaleUser.platform_chat_id == str(platform_chat_id),
            )
            .one_or_none()
        )

    def list_by_instance(self, instance_id: str) -> list[EnterpriseBaleUser]:
        """List users for an instance."""
        return (
            self.db.query(EnterpriseBaleUser)
            .filter(EnterpriseBaleUser.instance_id == str(instance_id))
            .order_by(EnterpriseBaleUser.updated_at.desc())
            .all()
        )

    def list_by_phone_number(self, instance_id: str, phone_number: str) -> list[EnterpriseBaleUser]:
        """List users for an instance by exact normalized phone number."""
        return (
            self.db.query(EnterpriseBaleUser)
            .filter(
                EnterpriseBaleUser.instance_id == str(instance_id),
                EnterpriseBaleUser.phone_number == str(phone_number),
            )
            .order_by(EnterpriseBaleUser.updated_at.desc())
            .all()
        )

    def save(self, row: EnterpriseBaleUser) -> EnterpriseBaleUser:
        """Persist a user row."""
        self.db.add(row)
        self.db.flush()
        return row
