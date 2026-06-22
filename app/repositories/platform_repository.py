"""
Module Overview
---------------
Purpose: Repository-layer data access helpers for persistence operations.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import PlatformType


class PlatformRepository:
    """Repository for platform persistence operations."""
    def __init__(self, db: Session):
        """Initialize the instance."""
        self.db = db

    def list_active(self) -> list[PlatformType]:
        """List active."""
        return (
            self.db.query(PlatformType)
            .filter(PlatformType.is_active.is_(True))
            .order_by(PlatformType.key.asc())
            .all()
        )

    def list_all(self) -> list[PlatformType]:
        """List all."""
        return self.db.query(PlatformType).order_by(PlatformType.key.asc()).all()

    def get_by_key(self, key: str) -> Optional[PlatformType]:
        """Get by key."""
        return self.db.query(PlatformType).filter(PlatformType.key == str(key)).one_or_none()

    def upsert(
        self,
        key: str,
        display_name: str,
        capabilities_json: dict,
        metadata_schema_json: dict,
        is_active: bool = True,
    ) -> PlatformType:
        """Create or update a platform type row."""
        row = self.get_by_key(key)
        if not row:
            row = PlatformType(
                key=key,
                display_name=display_name,
                capabilities_json=capabilities_json,
                metadata_schema_json=metadata_schema_json,
                is_active=is_active,
            )
            self.db.add(row)
            self.db.flush()
            return row

        row.display_name = display_name
        row.capabilities_json = capabilities_json
        row.metadata_schema_json = metadata_schema_json
        row.is_active = bool(is_active)
        self.db.flush()
        return row

