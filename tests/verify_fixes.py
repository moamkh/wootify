"""
Local verification script for post-fix validation.
Run with: python -m pytest tests/verify_fixes.py -v
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text

from app.utils.cache_utils import TTLCache


class TestTTLCache:
    """Verify pure-Python TTLCache behaves correctly."""

    def test_basic_get_set(self):
        cache: TTLCache[int] = TTLCache(maxsize=10, ttl=60)
        cache["a"] = 1
        assert cache.get("a") == 1
        assert "a" in cache

    def test_expiration(self):
        cache: TTLCache[int] = TTLCache(maxsize=10, ttl=0.1)
        cache["a"] = 1
        assert cache.get("a") == 1
        time.sleep(0.15)
        assert cache.get("a") is None
        assert "a" not in cache

    def test_maxsize_eviction(self):
        cache: TTLCache[int] = TTLCache(maxsize=3, ttl=60)
        cache["a"] = 1
        cache["b"] = 2
        cache["c"] = 3
        cache["d"] = 4
        # Oldest should be evicted
        assert cache.get("a") is None
        assert cache.get("d") == 4

    def test_getitem_raises_keyerror(self):
        cache: TTLCache[int] = TTLCache(maxsize=10, ttl=60)
        with pytest.raises(KeyError):
            _ = cache["missing"]


class TestDatabaseSchema:
    """Verify migrations produced the expected schema."""

    def test_composite_indexes_exist(self):
        engine = create_engine("sqlite:///./wootify.db")
        with engine.connect() as conn:
            indexes = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='index'")
                )
            }
        assert "ix_conversations_instance_platform_active" in indexes
        assert "ix_conversations_instance_chatwoot_active" in indexes
        assert "ix_enterprise_bale_sessions_user_route_status" in indexes
        assert "ix_enterprise_bale_users_instance_state" in indexes
        assert "ix_enterprise_document_assets_instance_type_active_sort" in indexes
        assert "ix_enterprise_telegram_sessions_user_route_status" in indexes
        assert "ix_enterprise_telegram_users_instance_state" in indexes

    def test_last_enterprise_sms_sync_at_column_exists(self):
        engine = create_engine("sqlite:///./wootify.db")
        with engine.connect() as conn:
            cols = {
                row[1]
                for row in conn.execute(
                    text("PRAGMA table_info(instance_runtime_state)")
                )
            }
        assert "last_enterprise_sms_sync_at" in cols

    def test_bale_pv_phone_resolved_users_table_exists(self):
        engine = create_engine("sqlite:///./wootify.db")
        with engine.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            }
        assert "bale_pv_phone_resolved_users" in tables


class TestEnterpriseBaleServiceNoSideEffects:
    """Verify _active_live_session_for_state no longer mutates."""

    def test_getter_does_not_set_user_present(self):
        from app.services.enterprise_bale_service import EnterpriseBaleService
        from app.models import EnterpriseBaleUser, EnterpriseUserState

        svc = EnterpriseBaleService()
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_session.user_present = False

        with patch.object(
            svc, "_sessions", return_value=MagicMock()
        ) as mock_repo_factory:
            mock_repo = MagicMock()
            mock_repo_factory.return_value = mock_repo
            mock_repo.get_unresolved_for_user_route.return_value = mock_session

            user = MagicMock(spec=EnterpriseBaleUser)
            user.current_state = EnterpriseUserState.live_customer_service
            user.id = "user-123"

            result = svc._active_live_session_for_state(mock_db, user)

            assert result is mock_session
            # The getter must NOT have mutated user_present
            assert mock_session.user_present is False
            mock_repo.get_unresolved_for_user_route.assert_called_once_with(
                "user-123", "customer_service"
            )

    def test_mark_user_present_explicit(self):
        from app.services.enterprise_bale_service import EnterpriseBaleService

        svc = EnterpriseBaleService()
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_session.user_present = False

        with patch.object(svc, "_sessions", return_value=MagicMock()) as mock_repo_factory:
            mock_repo = MagicMock()
            mock_repo_factory.return_value = mock_repo
            svc._mark_user_present(mock_db, mock_session)
            assert mock_session.user_present is True
            mock_repo.save.assert_called_once_with(mock_session)


class TestEnterpriseTelegramServiceNoSideEffects:
    """Verify _active_live_session no longer mutates."""

    def test_getter_does_not_set_user_present(self):
        from app.services.enterprise_telegram_service import EnterpriseTelegramService
        from app.models import EnterpriseTelegramUser

        svc = EnterpriseTelegramService()
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_session.user_present = False

        with patch.object(
            svc, "_sessions", return_value=MagicMock()
        ) as mock_repo_factory:
            mock_repo = MagicMock()
            mock_repo_factory.return_value = mock_repo
            mock_repo.get_unresolved_for_user_route.return_value = mock_session

            user = MagicMock(spec=EnterpriseTelegramUser)
            user.current_state = "live_customer_service"
            user.id = "user-123"

            with patch.object(svc, "_is_live_state", return_value=True):
                result = svc._active_live_session(mock_db, user)

            assert result is mock_session
            assert mock_session.user_present is False


class TestMenuLabelCaching:
    """Verify _known_menu_button_labels uses cache."""

    def test_bale_service_caches_labels(self):
        from app.services.enterprise_bale_service import EnterpriseBaleService

        svc = EnterpriseBaleService()
        mock_db = MagicMock()

        # First call should hit DB
        with patch.object(
            svc, "_manual_menu_markup", return_value={"keyboard": []}
        ):
            with patch.object(
                svc, "_manual_group_menu_markup", return_value={"keyboard": []}
            ):
                with patch.object(
                    svc, "_keyboard_items", return_value=[]
                ):
                    result1 = svc._known_menu_button_labels(mock_db, "inst-1")

        # Second call should return cached value without hitting DB
        with patch.object(
            svc, "_manual_menu_markup", side_effect=Exception("should not be called")
        ):
            result2 = svc._known_menu_button_labels(mock_db, "inst-1")

        assert result1 == result2


class TestBalePollingSmsBackground:
    """Verify SMS sync scheduling logic."""

    def test_sms_sync_runs_in_background_not_blocking(self):
        from app.services.bale_polling_service import BalePollingService

        svc = BalePollingService()
        svc._enterprise.sms_sync_enabled = MagicMock(return_value=True)
        svc._enterprise.sms_sync_interval_seconds = MagicMock(return_value=60)

        async def fake_sync(db, key):
            return {"fetched": 0, "delivered": 0, "dropped": 0, "failed": 0, "last_id": 1}

        svc._enterprise.sync_external_sms_messages = fake_sync

        async def fake_dump(**kwargs):
            pass

        svc._write_temp_sms_result_dump = fake_dump

        async def fake_update_runtime(*args, **kwargs):
            return True

        svc._update_runtime_state_with_retry = fake_update_runtime

        async def run_test():
            await svc._maybe_run_enterprise_sms_sync(
                "test-instance",
                platform_key="bale_enterprise",
                platform_metadata={},
                runtime_instance_id="inst-123",
            )

            # A background task should have been created
            assert "test-instance" in svc._enterprise_sms_sync_tasks
            task = svc._enterprise_sms_sync_tasks["test-instance"]
            assert isinstance(task, asyncio.Task)
            # Give it a moment to complete
            await asyncio.wait_for(task, timeout=2.0)

        asyncio.run(run_test())

    def test_sms_sync_skips_if_task_already_running(self):
        from app.services.bale_polling_service import BalePollingService

        svc = BalePollingService()
        svc._enterprise.sms_sync_enabled = MagicMock(return_value=True)
        svc._enterprise.sms_sync_interval_seconds = MagicMock(return_value=60)
        svc._enterprise_sms_last_run["test-instance"] = time.time()

        async def run_test():
            # Create a fake "running" task inside the event loop
            async def slow():
                await asyncio.sleep(10)

            svc._enterprise_sms_sync_tasks["test-instance"] = asyncio.create_task(slow())

            await svc._maybe_run_enterprise_sms_sync(
                "test-instance",
                platform_key="bale_enterprise",
                platform_metadata={},
            )

            # Should not have spawned a second task
            assert len(svc._enterprise_sms_sync_tasks) == 1
            svc._enterprise_sms_sync_tasks["test-instance"].cancel()
            try:
                await svc._enterprise_sms_sync_tasks["test-instance"]
            except asyncio.CancelledError:
                pass

        asyncio.run(run_test())


class TestImportSanity:
    """Fast import checks for all touched modules."""

    def test_all_services_import(self):
        from app.services.bale_polling_service import BalePollingService
        from app.services.bridge_service import BridgeService
        from app.services.enterprise_bale_service import EnterpriseBaleService
        from app.services.enterprise_telegram_service import EnterpriseTelegramService
        from app.services.enterprise_document_service import EnterpriseDocumentService
        from app.services.enterprise_gre_service import EnterpriseGreValidator
        from app.services.instance_service import InstanceService

        assert BalePollingService
        assert BridgeService
        assert EnterpriseBaleService
        assert EnterpriseTelegramService
        assert EnterpriseDocumentService
        assert EnterpriseGreValidator
        assert InstanceService


class TestBalePvImportContacts:
    """Verify ImportContacts protobuf builder round-trips."""

    def test_import_contacts_request_serializes(self):
        from bale_grpc_client.messaging_messages import ImportContactsRequest, PhoneContact
        from bale_grpc_client.protobuf_wire import ProtobufParser

        req = ImportContactsRequest(
            phones=[PhoneContact(phone_number=989136421196, name="Test")],
            optimizations=[],
        )
        data = req.serialize()
        fields = ProtobufParser(data).parse()
        # phones is field 1 (length-delimited message)
        assert 1 in fields
        # optimizations omitted when empty
        assert 3 not in fields

    def test_import_contacts_request_with_optimizations(self):
        from bale_grpc_client.messaging_messages import ImportContactsRequest, PhoneContact
        from bale_grpc_client.protobuf_wire import ProtobufParser

        req = ImportContactsRequest(
            phones=[PhoneContact(phone_number=989136421196)],
            optimizations=[1, 2],
        )
        data = req.serialize()
        fields = ProtobufParser(data).parse()
        assert 1 in fields
        assert 3 in fields
        # Packed repeated int32
        assert len(fields[3]) == 1

    def test_parse_import_contacts_response_handles_empty(self):
        from bale_grpc_client.dialog_parser import parse_import_contacts_response

        parsed = parse_import_contacts_response(b"")
        assert parsed["users"] == []
        assert parsed["seq"] == 0
        assert parsed["state"] == b""
