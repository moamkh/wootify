"""
Module Overview
---------------
Purpose: Repository for enterprise manual group persistence operations.
Documentation Standard: module/class/public-method docstrings.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import EnterpriseManualGroup


class EnterpriseManualGroupRepository:
    """Repository for enterprise manual groups."""

    def __init__(self, db: Session) -> None:
        """Initialize the repository with a database session."""
        self.db = db

    def get_by_id(self, group_id: str) -> Optional[EnterpriseManualGroup]:
        """Get a manual group by ID."""
        return self.db.get(EnterpriseManualGroup, str(group_id))

    def list_by_instance(
        self,
        instance_id: str,
        *,
        active_only: bool = True,
    ) -> list[EnterpriseManualGroup]:
        """List manual groups for an instance ordered by sort order."""
        query = self.db.query(EnterpriseManualGroup).filter(
            EnterpriseManualGroup.instance_id == instance_id
        )
        if active_only:
            query = query.filter(EnterpriseManualGroup.is_active.is_(True))
        return query.order_by(EnterpriseManualGroup.sort_order).all()

    def get_by_name(self, instance_id: str, name: str) -> Optional[EnterpriseManualGroup]:
        """Get a manual group by instance and name."""
        return self.db.query(EnterpriseManualGroup).filter(
            EnterpriseManualGroup.instance_id == instance_id,
            EnterpriseManualGroup.name == name,
        ).first()

    def next_sort_order(self, instance_id: str) -> int:
        """Get the next sort order for a group in this instance."""
        result = self.db.query(EnterpriseManualGroup).filter(
            EnterpriseManualGroup.instance_id == instance_id
        ).order_by(EnterpriseManualGroup.sort_order.desc()).first()

        if not result or result.sort_order is None:
            return 0
        return int(result.sort_order or 0) + 1

    def save(self, row: EnterpriseManualGroup) -> None:
        """Save/update a manual group."""
        self.db.add(row)
        self.db.flush()

    def delete(self, group_id: str) -> bool:
        """Delete a manual group by ID. Returns True if deleted."""
        row = self.get_by_id(group_id)
        if not row:
            return False
        self.db.delete(row)
        self.db.flush()
        return True
