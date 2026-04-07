"""
Module Overview
---------------
Purpose: Repository-layer data access helpers for persistence operations.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import FeatureDefinition


class FeatureRepository:
    """Repository for feature persistence operations."""
    def __init__(self, db: Session):
        """Initialize the instance."""
        self.db = db

    def list_all(self) -> list[FeatureDefinition]:
        """List all."""
        return self.db.query(FeatureDefinition).order_by(FeatureDefinition.key.asc()).all()

    def get(self, key: str) -> Optional[FeatureDefinition]:
        """Get feature definition by key."""
        return self.db.get(FeatureDefinition, str(key))

    def upsert(
        self,
        key: str,
        display_name: str,
        description: str,
        default_enabled: bool,
        required_platform_capability: Optional[str] = None,
        required_chatwoot_capability: Optional[str] = None,
    ) -> FeatureDefinition:
        """Create or update a feature definition row."""
        row = self.get(key)
        if not row:
            row = FeatureDefinition(
                key=key,
                display_name=display_name,
                description=description,
                default_enabled=bool(default_enabled),
                required_platform_capability=required_platform_capability,
                required_chatwoot_capability=required_chatwoot_capability,
            )
            self.db.add(row)
            self.db.flush()
            return row

        row.display_name = display_name
        row.description = description
        row.default_enabled = bool(default_enabled)
        row.required_platform_capability = required_platform_capability
        row.required_chatwoot_capability = required_chatwoot_capability
        self.db.flush()
        return row

