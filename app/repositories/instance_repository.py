"""
Module Overview
---------------
Purpose: Repository-layer data access helpers for persistence operations.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session, selectinload

from app.models import Instance


class InstanceRepository:
    """Repository for instance persistence operations."""
    def __init__(self, db: Session):
        """Initialize the instance."""
        self.db = db

    def list_all(self) -> list[Instance]:
        """List all with eagerly loaded relationships."""
        return (
            self.db.query(Instance)
            .options(
                selectinload(Instance.platform_type),
                selectinload(Instance.runtime_state),
                selectinload(Instance.feature_overrides),
            )
            .order_by(Instance.instance_key.asc())
            .all()
        )

    def get_by_key(self, instance_key: str) -> Optional[Instance]:
        """Get by key."""
        return self.db.query(Instance).filter(Instance.instance_key == str(instance_key)).one_or_none()

    def get_by_id(self, instance_id: str) -> Optional[Instance]:
        """Get by id."""
        return self.db.get(Instance, str(instance_id))

    def add(self, row: Instance) -> Instance:
        """Add."""
        self.db.add(row)
        self.db.flush()
        return row

    def delete(self, row: Instance) -> None:
        """Delete an instance row."""
        self.db.delete(row)
        self.db.flush()

    