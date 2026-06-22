"""
Module Overview
---------------
Purpose: Service-layer business logic for enterprise manual groups.
Documentation Standard: module/class/public-method docstrings.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from sqlalchemy.orm import Session, selectinload

from app.models import EnterpriseDocumentAsset, EnterpriseManualGroup, EnterpriseManualGroupAssignment
from app.repositories.enterprise_document_asset_repository import EnterpriseDocumentAssetRepository
from app.repositories.enterprise_manual_group_repository import EnterpriseManualGroupRepository
from app.repositories.instance_repository import InstanceRepository


class EnterpriseManualGroupService:
    """Service for manual group management."""

    def __init__(self) -> None:
        """Initialize repository factories."""
        self.instance_repository = InstanceRepository
        self.group_repository = EnterpriseManualGroupRepository

    def _get_instance(self, db: Session, instance_key: str):
        """Get instance by key or raise a domain error."""
        instance = self.instance_repository(db).get_by_key(instance_key)
        if not instance:
            raise ValueError('instance not found')
        return instance

    def list_groups(self, db: Session, instance_key: str) -> list[EnterpriseManualGroup]:
        """List all manual groups for an instance."""
        instance = self._get_instance(db, instance_key)
        repo = self.group_repository(db)
        return repo.list_by_instance(instance.id, active_only=True)

    def get_group(self, db: Session, instance_key: str, group_id: str) -> Optional[EnterpriseManualGroup]:
        """Get a manual group by ID."""
        instance = self._get_instance(db, instance_key)
        repo = self.group_repository(db)
        group = repo.get_by_id(group_id)
        if not group or group.instance_id != instance.id:
            return None
        return group

    def create_group(self, db: Session, instance_key: str, name: str) -> EnterpriseManualGroup:
        """Create a new manual group."""
        instance = self._get_instance(db, instance_key)

        repo = self.group_repository(db)
        
        # Check for duplicate name
        existing = repo.get_by_name(instance.id, name)
        if existing:
            raise ValueError(f'group with name "{name}" already exists')

        # Get next sort order
        sort_order = repo.next_sort_order(instance.id)

        # Create group
        group = EnterpriseManualGroup(
            id=str(uuid4()),
            instance_id=instance.id,
            name=name,
            sort_order=sort_order,
            is_active=True,
        )
        repo.save(group)
        db.commit()
        return group

    def rename_group(self, db: Session, instance_key: str, group_id: str, name: str) -> EnterpriseManualGroup:
        """Rename a manual group."""
        instance = self._get_instance(db, instance_key)

        repo = self.group_repository(db)
        group = repo.get_by_id(group_id)
        if not group or group.instance_id != instance.id:
            raise ValueError('group not found')

        # Check for duplicate name (excluding current group)
        existing = repo.get_by_name(instance.id, name)
        if existing and existing.id != group.id:
            raise ValueError(f'group with name "{name}" already exists')

        # Update name
        group.name = name
        repo.save(group)
        db.commit()
        return group

    def delete_group(self, db: Session, instance_key: str, group_id: str) -> bool:
        """Delete a manual group."""
        instance = self._get_instance(db, instance_key)

        repo = self.group_repository(db)
        group = repo.get_by_id(group_id)
        if not group or group.instance_id != instance.id:
            return False

        success = repo.delete(group_id)
        if success:
            db.commit()
        return success

    def list_group_manuals(
        self,
        db: Session,
        instance_key: str,
        group_id: str,
    ) -> list[EnterpriseDocumentAsset]:
        """List manuals assigned to a group."""
        instance = self._get_instance(db, instance_key)

        repo = self.group_repository(db)
        group = repo.get_by_id(group_id)
        if not group or group.instance_id != instance.id:
            raise ValueError('group not found')

        asset_repo = EnterpriseDocumentAssetRepository(db)
        return asset_repo.list_by_group(group_id, active_only=True)

    def list_groups_with_manuals(
        self,
        db: Session,
        instance_key: str,
    ) -> dict[str, Any]:
        """List all manual groups for an instance with their manuals in a single query batch.

        Returns a dict with:
        - groups: list of groups, each with 'manuals' list
        - manual_group_map: dict mapping manual asset id -> group id
        """
        instance = self._get_instance(db, instance_key)

        groups = (
            db.query(EnterpriseManualGroup)
            .filter(
                EnterpriseManualGroup.instance_id == instance.id,
                EnterpriseManualGroup.is_active.is_(True),
            )
            .order_by(EnterpriseManualGroup.sort_order)
            .options(
                selectinload(EnterpriseManualGroup.assignments)
                .selectinload(EnterpriseManualGroupAssignment.asset)
            )
            .all()
        )

        result_groups = []
        manual_group_map: dict[str, str] = {}

        for group in groups:
            manuals = []
            for assignment in group.assignments:
                asset = assignment.asset
                if asset and asset.is_active:
                    manuals.append(asset)
                    manual_group_map[asset.id] = group.id
            result_groups.append({
                'id': group.id,
                'name': group.name,
                'sort_order': group.sort_order,
                'is_active': group.is_active,
                'created_at': group.created_at,
                'updated_at': group.updated_at,
                'manuals': manuals,
            })

        return {
            'groups': result_groups,
            'manual_group_map': manual_group_map,
        }

    def add_manual_to_group(
        self,
        db: Session,
        instance_key: str,
        group_id: str,
        asset_id: str,
    ) -> EnterpriseManualGroupAssignment:
        """Add a manual to a group."""
        instance = self._get_instance(db, instance_key)

        repo = self.group_repository(db)
        group = repo.get_by_id(group_id)
        if not group or group.instance_id != instance.id:
            raise ValueError('group not found')

        # Verify asset exists and belongs to instance
        asset_repo = EnterpriseDocumentAssetRepository(db)
        asset = asset_repo.get_by_id(asset_id)
        if not asset or asset.instance_id != instance.id:
            raise ValueError('asset not found')

        # Check if already assigned
        existing = db.query(EnterpriseManualGroupAssignment).filter(
            EnterpriseManualGroupAssignment.group_id == group_id,
            EnterpriseManualGroupAssignment.asset_id == asset_id,
        ).first()
        if existing:
            raise ValueError('manual already assigned to this group')

        # Get next sort order for this group
        next_order = db.query(EnterpriseManualGroupAssignment).filter(
            EnterpriseManualGroupAssignment.group_id == group_id,
        ).order_by(EnterpriseManualGroupAssignment.sort_order.desc()).first()
        sort_order = (int(next_order.sort_order or 0) + 1) if next_order else 0

        # Create assignment
        assignment = EnterpriseManualGroupAssignment(
            id=str(uuid4()),
            group_id=group_id,
            asset_id=asset_id,
            sort_order=sort_order,
        )
        db.add(assignment)
        db.commit()
        return assignment

    def remove_manual_from_group(
        self,
        db: Session,
        instance_key: str,
        group_id: str,
        asset_id: str,
    ) -> bool:
        """Remove a manual from a group."""
        instance = self._get_instance(db, instance_key)

        repo = self.group_repository(db)
        group = repo.get_by_id(group_id)
        if not group or group.instance_id != instance.id:
            raise ValueError('group not found')

        # Find and delete the assignment
        assignment = db.query(EnterpriseManualGroupAssignment).filter(
            EnterpriseManualGroupAssignment.group_id == group_id,
            EnterpriseManualGroupAssignment.asset_id == asset_id,
        ).first()
        if not assignment:
            return False

        db.delete(assignment)
        db.commit()
        return True
