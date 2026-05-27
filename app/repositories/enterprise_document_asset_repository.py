"""
Module Overview
---------------
Purpose: Repository-layer data access helpers for enterprise document assets.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session, selectinload

from app.models import EnterpriseDocumentAsset, EnterpriseDocumentAssetType, EnterpriseManualGroupAssignment


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
        self.db.query(EnterpriseDocumentAsset).filter(
            EnterpriseDocumentAsset.instance_id == str(instance_id),
            EnterpriseDocumentAsset.asset_type == EnterpriseDocumentAssetType.catalog,
            EnterpriseDocumentAsset.is_active.is_(True),
        ).update({EnterpriseDocumentAsset.is_active: False}, synchronize_session=False)

    def next_sort_order(self, instance_id: str, asset_type: EnterpriseDocumentAssetType) -> int:
        """Compute the next sort order for an instance asset type."""
        from sqlalchemy import func
        result = (
            self.db.query(func.max(EnterpriseDocumentAsset.sort_order))
            .filter(
                EnterpriseDocumentAsset.instance_id == str(instance_id),
                EnterpriseDocumentAsset.asset_type == asset_type,
            )
            .scalar()
        )
        return (int(result or 0) + 1) if result else 1

    def save(self, row: EnterpriseDocumentAsset) -> EnterpriseDocumentAsset:
        """Persist an asset row."""
        self.db.add(row)
        self.db.flush()
        return row

    def list_by_group(
        self,
        group_id: str,
        *,
        active_only: bool = True,
    ) -> list[EnterpriseDocumentAsset]:
        """List manuals assigned to a group, ordered by assignment sort order."""
        query = (
            self.db.query(EnterpriseDocumentAsset)
            .join(EnterpriseManualGroupAssignment)
            .filter(EnterpriseManualGroupAssignment.group_id == group_id)
        )
        if active_only:
            query = query.filter(EnterpriseDocumentAsset.is_active.is_(True))
        return (
            query.options(selectinload(EnterpriseDocumentAsset.group_assignments))
            .order_by(EnterpriseManualGroupAssignment.sort_order)
            .all()
        )

    def list_unassigned_for_instance(
        self,
        instance_id: str,
        *,
        active_only: bool = True,
    ) -> list[EnterpriseDocumentAsset]:
        """List manuals not assigned to any group for an instance."""
        query = (
            self.db.query(EnterpriseDocumentAsset)
            .filter(
                EnterpriseDocumentAsset.instance_id == instance_id,
                EnterpriseDocumentAsset.asset_type == EnterpriseDocumentAssetType.manual,
                ~self.db.query(EnterpriseManualGroupAssignment).filter(
                    EnterpriseManualGroupAssignment.asset_id == EnterpriseDocumentAsset.id
                ).exists(),
            )
        )
        if active_only:
            query = query.filter(EnterpriseDocumentAsset.is_active.is_(True))
        return query.order_by(EnterpriseDocumentAsset.sort_order, EnterpriseDocumentAsset.created_at).all()
