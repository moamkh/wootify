"""
Module Overview
---------------
Purpose: Repository-layer data access helpers for enterprise document assets.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models import EnterpriseDocumentAsset, EnterpriseDocumentAssetType


class EnterpriseDocumentAssetRepository:
    """Repository for enterprise document asset persistence operations."""

    def __init__(self, db: Session):
        """Initialize the instance."""
        self.db = db

    def get_by_id(self, asset_id: str) -> Optional[EnterpriseDocumentAsset]:
        """Get an asset by id."""
        return self.db.get(EnterpriseDocumentAsset, str(asset_id))

    def list_for_instance(
        self,
        instance_id: str,
        *,
        asset_type: Optional[EnterpriseDocumentAssetType] = None,
        active_only: bool = True,
    ) -> list[EnterpriseDocumentAsset]:
        """List assets for an instance."""
        query = self.db.query(EnterpriseDocumentAsset).filter(EnterpriseDocumentAsset.instance_id == str(instance_id))
        if asset_type is not None:
            query = query.filter(EnterpriseDocumentAsset.asset_type == asset_type)
        if active_only:
            query = query.filter(EnterpriseDocumentAsset.is_active.is_(True))
        return query.order_by(EnterpriseDocumentAsset.sort_order.asc(), EnterpriseDocumentAsset.created_at.asc()).all()

    def get_active_catalog(self, instance_id: str) -> Optional[EnterpriseDocumentAsset]:
        """Get the active catalog for an instance."""
        return (
            self.db.query(EnterpriseDocumentAsset)
            .filter(
                EnterpriseDocumentAsset.instance_id == str(instance_id),
                EnterpriseDocumentAsset.asset_type == EnterpriseDocumentAssetType.catalog,
                EnterpriseDocumentAsset.is_active.is_(True),
            )
            .order_by(EnterpriseDocumentAsset.updated_at.desc(), EnterpriseDocumentAsset.created_at.desc())
            .first()
        )

    def deactivate_catalogs(self, instance_id: str) -> None:
        """Mark all catalogs for an instance inactive."""
        rows = (
            self.db.query(EnterpriseDocumentAsset)
            .filter(
                EnterpriseDocumentAsset.instance_id == str(instance_id),
                EnterpriseDocumentAsset.asset_type == EnterpriseDocumentAssetType.catalog,
                EnterpriseDocumentAsset.is_active.is_(True),
            )
            .all()
        )
        for row in rows:
            row.is_active = False

    def next_sort_order(self, instance_id: str, asset_type: EnterpriseDocumentAssetType) -> int:
        """Compute the next sort order for an instance asset type."""
        rows = self.list_for_instance(instance_id, asset_type=asset_type, active_only=False)
        if not rows:
            return 1
        return max(int(row.sort_order or 0) for row in rows) + 1

    def save(self, row: EnterpriseDocumentAsset) -> EnterpriseDocumentAsset:
        """Persist an asset row."""
        self.db.add(row)
        self.db.flush()
        return row
