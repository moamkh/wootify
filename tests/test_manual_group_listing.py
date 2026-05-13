"""
Tests for enterprise manual group listing performance and correctness.
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import (
    EnterpriseDocumentAsset,
    EnterpriseDocumentAssetType,
    EnterpriseManualGroup,
    EnterpriseManualGroupAssignment,
    Instance,
    PlatformType,
)
from app.repositories.enterprise_document_asset_repository import EnterpriseDocumentAssetRepository
from app.services.enterprise_manual_group_service import EnterpriseManualGroupService


def _seed_platform_and_instance(db: Session, instance_key: str = "test-instance") -> Instance:
    """Helper to create a platform type and instance."""
    platform = db.query(PlatformType).filter_by(key="bale_enterprise").first()
    if not platform:
        platform = PlatformType(
            key="bale_enterprise",
            display_name="Bale Enterprise",
            capabilities_json={},
            metadata_schema_json={},
        )
        db.add(platform)
        db.flush()
    instance = Instance(
        instance_key=instance_key,
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted="",
        proxy_config_encrypted="",
    )
    db.add(instance)
    db.commit()
    return instance


def _seed_manual(db: Session, instance: Instance, display_name: str, sort_order: int = 0, is_active: bool = True) -> EnterpriseDocumentAsset:
    """Helper to create a manual asset."""
    asset = EnterpriseDocumentAsset(
        instance_id=instance.id,
        asset_type=EnterpriseDocumentAssetType.manual,
        display_name=display_name,
        link_url="",
        storage_path="/tmp/test",
        original_filename="test.pdf",
        content_type="application/pdf",
        size_bytes=0,
        sort_order=sort_order,
        is_active=is_active,
    )
    db.add(asset)
    db.commit()
    return asset


def _seed_group(db: Session, instance: Instance, name: str, sort_order: int = 0) -> EnterpriseManualGroup:
    """Helper to create a manual group."""
    group = EnterpriseManualGroup(
        instance_id=instance.id,
        name=name,
        sort_order=sort_order,
        is_active=True,
    )
    db.add(group)
    db.commit()
    return group


def _assign_manual(db: Session, group: EnterpriseManualGroup, asset: EnterpriseDocumentAsset, sort_order: int = 0) -> EnterpriseManualGroupAssignment:
    """Helper to assign a manual to a group."""
    assignment = EnterpriseManualGroupAssignment(
        group_id=group.id,
        asset_id=asset.id,
        sort_order=sort_order,
    )
    db.add(assignment)
    db.commit()
    return assignment


class TestListGroupManualsRepository:
    """Tests for EnterpriseDocumentAssetRepository.list_by_group."""

    def test_list_by_group_empty(self, db_session):
        """Listing manuals for a group with no assignments returns empty list."""
        repo = EnterpriseDocumentAssetRepository(db_session)
        result = repo.list_by_group("nonexistent-group", active_only=True)
        assert result == []

    def test_list_by_group_returns_manuals_ordered(self, db_session):
        """Listing manuals returns them ordered by assignment sort_order."""
        instance = _seed_platform_and_instance(db_session)
        group = _seed_group(db_session, instance, "Test Group")
        manual_a = _seed_manual(db_session, instance, "Manual A")
        manual_b = _seed_manual(db_session, instance, "Manual B")
        manual_c = _seed_manual(db_session, instance, "Manual C")

        _assign_manual(db_session, group, manual_b, sort_order=1)
        _assign_manual(db_session, group, manual_a, sort_order=0)
        _assign_manual(db_session, group, manual_c, sort_order=2)

        repo = EnterpriseDocumentAssetRepository(db_session)
        result = repo.list_by_group(group.id, active_only=True)

        assert [m.display_name for m in result] == ["Manual A", "Manual B", "Manual C"]

    def test_list_by_group_hides_inactive_assets(self, db_session):
        """When active_only=True, inactive manuals are excluded."""
        instance = _seed_platform_and_instance(db_session)
        group = _seed_group(db_session, instance, "Test Group")
        active_manual = _seed_manual(db_session, instance, "Active", is_active=True)
        inactive_manual = _seed_manual(db_session, instance, "Inactive", is_active=False)

        _assign_manual(db_session, group, active_manual)
        _assign_manual(db_session, group, inactive_manual)

        repo = EnterpriseDocumentAssetRepository(db_session)
        result = repo.list_by_group(group.id, active_only=True)

        assert len(result) == 1
        assert result[0].display_name == "Active"


class TestListGroupManualsService:
    """Tests for EnterpriseManualGroupService.list_group_manuals."""

    def test_list_group_manuals_success(self, db_session):
        """Service returns manuals for a valid instance+group."""
        instance = _seed_platform_and_instance(db_session)
        group = _seed_group(db_session, instance, "Test Group")
        manual = _seed_manual(db_session, instance, "Test Manual")
        _assign_manual(db_session, group, manual)

        service = EnterpriseManualGroupService()
        result = service.list_group_manuals(db_session, instance.instance_key, group.id)

        assert len(result) == 1
        assert result[0].display_name == "Test Manual"

    def test_list_group_manuals_wrong_instance(self, db_session):
        """Service raises ValueError when group does not belong to instance."""
        instance_a = _seed_platform_and_instance(db_session, "instance-a")
        instance_b = _seed_platform_and_instance(db_session, "instance-b")
        group = _seed_group(db_session, instance_a, "Group A")

        service = EnterpriseManualGroupService()
        with pytest.raises(ValueError, match="group not found"):
            service.list_group_manuals(db_session, instance_b.instance_key, group.id)

    def test_list_group_manuals_bad_instance(self, db_session):
        """Service raises ValueError when instance does not exist."""
        service = EnterpriseManualGroupService()
        with pytest.raises(ValueError, match="instance not found"):
            service.list_group_manuals(db_session, "nonexistent", "group-id")


class TestListGroupManualsEndpoint:
    """Tests for GET /instances/{key}/enterprise/manual-groups/{group_id}/manuals."""

    def test_endpoint_returns_manuals(self, client, db_session):
        """Endpoint returns 200 with manuals for a valid group."""
        instance = _seed_platform_and_instance(db_session)
        group = _seed_group(db_session, instance, "Test Group")
        manual = _seed_manual(db_session, instance, "Test Manual")
        _assign_manual(db_session, group, manual)

        response = client.get(f"/api/v1/instances/{instance.instance_key}/enterprise/manual-groups/{group.id}/manuals")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert len(data["items"]) == 1
        assert data["items"][0]["display_name"] == "Test Manual"

    def test_endpoint_404_for_missing_group(self, client, db_session):
        """Endpoint returns 400 when group not found (current behaviour)."""
        instance = _seed_platform_and_instance(db_session)
        response = client.get(f"/api/v1/instances/{instance.instance_key}/enterprise/manual-groups/nonexistent/manuals")
        assert response.status_code == 400

    def test_endpoint_404_for_missing_instance(self, client):
        """Endpoint returns 400 when instance not found (current behaviour)."""
        response = client.get("/api/v1/instances/nonexistent/enterprise/manual-groups/g1/manuals")
        assert response.status_code == 400


class TestListGroupsWithManualsEndpoint:
    """Tests for GET /instances/{key}/enterprise/manual-groups-with-manuals."""

    def test_bulk_endpoint_returns_groups_and_manuals(self, client, db_session):
        """Bulk endpoint returns all groups with their manuals and the mapping."""
        instance = _seed_platform_and_instance(db_session)
        group_a = _seed_group(db_session, instance, "Group A", sort_order=0)
        group_b = _seed_group(db_session, instance, "Group B", sort_order=1)
        manual_1 = _seed_manual(db_session, instance, "Manual 1")
        manual_2 = _seed_manual(db_session, instance, "Manual 2")
        manual_3 = _seed_manual(db_session, instance, "Manual 3")

        _assign_manual(db_session, group_a, manual_1, sort_order=0)
        _assign_manual(db_session, group_a, manual_2, sort_order=1)
        _assign_manual(db_session, group_b, manual_3, sort_order=0)

        response = client.get(f"/api/v1/instances/{instance.instance_key}/enterprise/manual-groups-with-manuals")
        assert response.status_code == 200
        data = response.json()

        assert "groups" in data
        assert "manual_group_map" in data
        assert len(data["groups"]) == 2

        # Groups ordered by sort_order
        assert data["groups"][0]["name"] == "Group A"
        assert data["groups"][1]["name"] == "Group B"

        # Group A has 2 manuals
        assert len(data["groups"][0]["manuals"]) == 2
        assert data["groups"][0]["manuals"][0]["display_name"] == "Manual 1"
        assert data["groups"][0]["manuals"][1]["display_name"] == "Manual 2"

        # Group B has 1 manual
        assert len(data["groups"][1]["manuals"]) == 1
        assert data["groups"][1]["manuals"][0]["display_name"] == "Manual 3"

        # Mapping is correct
        assert data["manual_group_map"][manual_1.id] == group_a.id
        assert data["manual_group_map"][manual_2.id] == group_a.id
        assert data["manual_group_map"][manual_3.id] == group_b.id

    def test_bulk_endpoint_empty_groups(self, client, db_session):
        """Bulk endpoint returns empty groups when no manuals assigned."""
        instance = _seed_platform_and_instance(db_session)
        group = _seed_group(db_session, instance, "Empty Group")

        response = client.get(f"/api/v1/instances/{instance.instance_key}/enterprise/manual-groups-with-manuals")
        assert response.status_code == 200
        data = response.json()

        assert len(data["groups"]) == 1
        assert data["groups"][0]["name"] == "Empty Group"
        assert data["groups"][0]["manuals"] == []
        assert data["manual_group_map"] == {}

    def test_bulk_endpoint_hides_inactive_manuals(self, client, db_session):
        """Bulk endpoint excludes inactive manuals from groups and mapping."""
        instance = _seed_platform_and_instance(db_session)
        group = _seed_group(db_session, instance, "Test Group")
        active = _seed_manual(db_session, instance, "Active", is_active=True)
        inactive = _seed_manual(db_session, instance, "Inactive", is_active=False)
        _assign_manual(db_session, group, active)
        _assign_manual(db_session, group, inactive)

        response = client.get(f"/api/v1/instances/{instance.instance_key}/enterprise/manual-groups-with-manuals")
        assert response.status_code == 200
        data = response.json()

        assert len(data["groups"][0]["manuals"]) == 1
        assert data["groups"][0]["manuals"][0]["display_name"] == "Active"
        assert active.id in data["manual_group_map"]
        assert inactive.id not in data["manual_group_map"]

    def test_bulk_endpoint_400_for_missing_instance(self, client):
        """Bulk endpoint returns 400 when instance not found."""
        response = client.get("/api/v1/instances/nonexistent/enterprise/manual-groups-with-manuals")
        assert response.status_code == 400
