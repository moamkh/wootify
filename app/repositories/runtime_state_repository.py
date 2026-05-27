"""
Module Overview
---------------
Purpose: Repository-layer data access helpers for persistence operations.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import InstanceRuntimeState


class RuntimeStateRepository:
    """Repository for runtime state persistence operations."""
    def __init__(self, db: Session):
        """Initialize the instance."""
        self.db = db

    def get(self, instance_id: str) -> Optional[InstanceRuntimeState]:
        """Get instance runtime state by instance id."""
        return self.db.get(InstanceRuntimeState, str(instance_id))

    def get_or_create(self, instance_id: str) -> InstanceRuntimeState:
        """Get or create."""
        row = self.get(instance_id)
        if row:
            return row
        row = InstanceRuntimeState(instance_id=str(instance_id))
        self.db.add(row)
        self.db.flush()
        return row

    def save(self, row: InstanceRuntimeState) -> InstanceRuntimeState:
        """Persist instance runtime state changes."""
        self.db.add(row)
        return row

