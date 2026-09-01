"""Unit tests for the Bale PV adapter and Chatwoot bridge helpers."""

from __future__ import annotations

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from wootify.connectors.bale_pv_connector import (
    bale_pv as bale_pv_connector,
    BalePvConnector,
    BalePvInstanceRuntime,
)

from wootify.adapters.bale_pv import BalePvAdapter
from wootify.models import BalePvPhoneResolvedUser, Conversation, Instance, PlatformType
from wootify.services.chatwoot_bridge_service import ChatwootBridgeService, chatwoot_bridge


def test_adapter_normalize_private_message():
    adapter = BalePvAdapter("test", {"bale_pv_phone_number": "989136421196"})
    raw = {
        "update_id": 123,
        "message": {
            "message_id": "456",
            "date": 1,
            "chat": {"id": "1755271951", "type": "private", "title": "User 1755271951"},
            "from": {"id": 1755271951, "first_name": "Amin", "username": "amin_user"},
            "text": "hello",
        },
    }
    event = adapter.normalize_incoming_update(raw)
    assert event is not None
    assert event["chat_id"] == "1755271951"
    assert event["chat_type"] == "private"
    assert event["from_name"] == "Amin"
    assert event["text"] == "hello"
    assert event["message_id"] == "456"
    assert event["sender_username"] == "amin_user"
    assert event["outgoing"] is False


def test_adapter_normalize_group_message():
    adapter = BalePvAdapter("test", {"bale_pv_phone_number": "989136421196"})
    raw = {
        "update_id": 124,
        "message": {
            "message_id": "457",
            "date": 1,
            "chat": {"id": "9287928", "type": "group", "title": "Test Group"},
            "from": {"id": 1688613407, "first_name": "User 1688613407", "username": "user_1688613407"},
            "text": "User 1688613407: hi all",
            "_sender_access_hash": 123456789,
        },
    }
    event = adapter.normalize_incoming_update(raw)
    assert event is not None
    assert event["chat_id"] == "9287928"
    assert event["chat_type"] == "group"
    # Group contact should be named after the group, not the sender.
    assert event["from_name"] == "Test Group"
    assert event["text"] == "User 1688613407: hi all"
    assert event["sender_id"] == "1688613407"
    assert adapter._access_hash_cache.get("1688613407") == 123456789


def test_adapter_normalize_outgoing_message():
    adapter = BalePvAdapter("test", {"bale_pv_phone_number": "989136421196"})
    raw = {
        "update_id": 125,
        "message": {
            "message_id": "458",
            "date": 1,
            "chat": {"id": "1755271951", "type": "private"},
            "from": {"id": 1755271951, "first_name": "Amin"},
            "text": "outgoing",
            "_outgoing": True,
        },
    }
    event = adapter.normalize_incoming_update(raw)
    assert event is not None
    assert event["outgoing"] is True


def test_extract_attachment_refs_prefers_document_filename():
    adapter = BalePvAdapter("test", {})
    message = {
        "document": {
            "file_id": '{"file_id":123,"access_hash":456,"peer_id":789,"file_name":"report.pdf"}',
            "file_name": "report.pdf",
            "mime_type": "application/pdf",
        },
    }
    refs = adapter._extract_attachment_refs(message)
    assert len(refs) == 1
    assert refs[0]["filename"] == "report.pdf"
    assert refs[0]["content_type"] == "application/pdf"


@pytest.mark.anyio
async def test_resolve_attachments_uses_content_type_from_connector():
    """Connector returns (content, content_type, file_path)."""
    adapter = BalePvAdapter("test", {})
    attachments = [
        {
            "file_id": '{"file_id":123,"access_hash":456,"peer_id":789}',
            "filename": "report.pdf",
            "content_type": "application/pdf",
        }
    ]
    with patch.object(
        adapter,
        "_normalize_content_type",
        wraps=adapter._normalize_content_type,
    ) as normalize_mock, patch(
        "wootify.adapters.bale_pv.bale_pv.download_file_by_id",
        new=AsyncMock(return_value=(b"%PDF-1.4", "application/pdf", "/tmp/report.pdf")),
    ):
        resolved = await adapter.resolve_attachments(attachments)
    assert len(resolved) == 1
    assert resolved[0]["content"] == b"%PDF-1.4"
    assert resolved[0]["filename"] == "report.pdf"
    # _normalize_content_type should be called with the connector's content_type.
    normalize_mock.assert_called_once()
    call_kwargs = normalize_mock.call_args.kwargs
    assert call_kwargs["content_type"] == "application/pdf"


def test_extract_peer_id_from_identifier():
    payload = {
        "conversation": {
            "meta": {"sender": {"identifier": "9287928", "phone_number": "989136421196"}}
        }
    }
    assert ChatwootBridgeService._extract_peer_id(payload) == "9287928"


def test_extract_peer_id_from_phone():
    payload = {
        "conversation": {
            "meta": {"sender": {"phone_number": "+989136421196"}}
        }
    }
    assert ChatwootBridgeService._extract_peer_id(payload) == "989136421196"


def test_extract_peer_id_strips_source_prefix():
    payload = {
        "conversation": {
            "meta": {"sender": {"identifier": "BALE_PV:1755271951"}}
        }
    }
    assert ChatwootBridgeService._extract_peer_id(payload) == "1755271951"


def test_extract_source_id_top_level():
    assert ChatwootBridgeService._extract_source_id({"source_id": "BALE_PV:123"}) == "BALE_PV:123"


def test_extract_source_id_from_conversation_message():
    payload = {
        "conversation": {
            "messages": [{"id": 1, "source_id": "BALE_PV:456"}]
        }
    }
    assert ChatwootBridgeService._extract_source_id(payload) == "BALE_PV:456"


def test_extract_source_id_missing():
    assert ChatwootBridgeService._extract_source_id({"conversation": {"messages": [{}]}}) is None
    assert ChatwootBridgeService._extract_source_id("not-a-dict") is None


def test_adapter_outgoing_private_uses_recipient_name_from_cache():
    adapter = BalePvAdapter("test", {"bale_pv_phone_number": "989136421196"})
    raw = {
        "update_id": 125,
        "message": {
            "message_id": "458",
            "date": 1,
            "chat": {"id": "770408072", "type": "private"},
            "from": {"id": 1755271951, "first_name": "Agent Nickname"},
            "text": "outgoing",
            "_outgoing": True,
        },
    }
    with patch.object(bale_pv_connector, "get_user_name", return_value="Real Contact Name"):
        event = adapter.normalize_incoming_update(raw)
    assert event is not None
    assert event["outgoing"] is True
    assert event["from_name"] == "Real Contact Name"


def test_adapter_outgoing_private_fallback_when_no_cached_name():
    adapter = BalePvAdapter("test", {"bale_pv_phone_number": "989136421196"})
    raw = {
        "update_id": 126,
        "message": {
            "message_id": "459",
            "date": 1,
            "chat": {"id": "770408072", "type": "private"},
            "from": {"id": 1755271951, "first_name": "Agent Nickname"},
            "text": "outgoing",
            "_outgoing": True,
        },
    }
    with patch.object(bale_pv_connector, "get_user_name", return_value=None):
        event = adapter.normalize_incoming_update(raw)
    assert event is not None
    assert event["outgoing"] is True
    assert event["from_name"] == "Bale User 770408072"


def test_adapter_incoming_private_still_uses_sender_name():
    adapter = BalePvAdapter("test", {"bale_pv_phone_number": "989136421196"})
    raw = {
        "update_id": 127,
        "message": {
            "message_id": "460",
            "date": 1,
            "chat": {"id": "770408072", "type": "private"},
            "from": {"id": 770408072, "first_name": "Contact Name", "username": "contact_user"},
            "text": "hello",
        },
    }
    event = adapter.normalize_incoming_update(raw)
    assert event is not None
    assert event["outgoing"] is False
    assert event["from_name"] == "Contact Name"


@pytest.mark.anyio
async def test_webhook_resolves_phone_to_bale_user(db_session):
    """When Chatwoot only provides a phone number, the bridge resolves it via
    the Bale adapter before sending the message.
    """
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-phone",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token"}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    resolved_user = BalePvPhoneResolvedUser(
        phone_number="989136421196",
        bale_user_id=12345,
        access_hash="67890",
        name="Amin",
        nick="amin_user",
        instance_id=instance.id,
    )
    db_session.add(resolved_user)
    db_session.commit()

    adapter = AsyncMock()
    adapter.send_text = AsyncMock(return_value={"ok": True})
    adapter.cache_access_hash = MagicMock()

    runtime = MagicMock()
    runtime.platform_type = "bale_pv_enterprise"
    runtime.status = "open"
    runtime.adapter = adapter

    client = AsyncMock()

    payload = {
        "event": "message_created",
        "message_type": "outgoing",
        "content": "Hello",
        "conversation": {
            "meta": {
                "sender": {
                    "id": 42,
                    "phone_number": "+989136421196",
                }
            },
        },
    }

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            result = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-phone", payload
            )

    assert result["ok"] is True
    assert result["peer_id"] == "12345"
    adapter.send_text.assert_awaited_once_with("12345", "Hello", reply_to=None, mirror_echo=False)


@pytest.mark.anyio
async def test_webhook_uses_cached_phone_resolution(db_session):
    """Phone resolution should be cached so the DB is not queried on every reply."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-cache",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token"}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    resolved_user = BalePvPhoneResolvedUser(
        phone_number="989136421196",
        bale_user_id=12345,
        access_hash="67890",
        name="Amin",
        nick="amin_user",
        instance_id=instance.id,
    )
    db_session.add(resolved_user)
    db_session.commit()

    adapter = AsyncMock()
    adapter.send_text = AsyncMock(return_value={"ok": True})
    adapter.cache_access_hash = MagicMock()

    runtime = MagicMock()
    runtime.platform_type = "bale_pv_enterprise"
    runtime.status = "open"
    runtime.adapter = adapter

    client = AsyncMock()

    payload = {
        "event": "message_created",
        "message_type": "outgoing",
        "content": "Hello again",
        "conversation": {
            "meta": {
                "sender": {
                    "id": 42,
                    "phone_number": "+989136421196",
                }
            },
        },
    }

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            # First call
            await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-cache", payload
            )
            # Second call should use cache
            await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-cache", payload
            )

    adapter.send_text.assert_awaited()
    assert adapter.send_text.await_count == 2


@pytest.mark.anyio
async def test_webhook_does_not_resolve_when_identifier_present(db_session):
    """When the Chatwoot contact already has an identifier, send using it directly
    without invoking phone resolution."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-ident",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token"}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    adapter = AsyncMock()
    adapter.send_text = AsyncMock(return_value={"ok": True})
    adapter.cache_access_hash = MagicMock()

    runtime = MagicMock()
    runtime.platform_type = "bale_pv_enterprise"
    runtime.status = "open"
    runtime.adapter = adapter

    client = AsyncMock()

    payload = {
        "event": "message_created",
        "message_type": "outgoing",
        "content": "Hello",
        "conversation": {
            "meta": {
                "sender": {
                    "id": 42,
                    "identifier": "BALE_PV:770408072",
                    "phone_number": "+989136421196",
                }
            },
        },
    }

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            result = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-ident", payload
            )

    assert result["ok"] is True
    assert result["peer_id"] == "770408072"
    adapter.resolve_phone_to_user.assert_not_awaited()
    adapter.send_text.assert_awaited_once_with("770408072", "Hello", reply_to=None, mirror_echo=False)
    client.update_contact.assert_not_awaited()


@pytest.mark.anyio
async def test_webhook_outbound_persists_conversation_mapping(db_session):
    """Panel-initiated (Chatvand pin) conversations must be mapped locally so
    the user's Bale reply lands in the same Chatwoot conversation instead of
    opening a duplicate."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-pin",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    adapter = AsyncMock()
    adapter.send_text = AsyncMock(return_value={"ok": True})

    runtime = MagicMock()
    runtime.platform_type = "bale_pv_enterprise"
    runtime.status = "open"
    runtime.adapter = adapter

    client = AsyncMock()

    payload = {
        "event": "message_created",
        "message_type": "outgoing",
        "content": "Hello from the panel",
        "conversation": {
            "id": 70,
            "inbox_id": 5,
            "meta": {
                "sender": {
                    "id": 42,
                    "identifier": "BALE_PV:770408072",
                }
            },
            "messages": [],
        },
    }

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            result = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-pin", payload
            )

    assert result["ok"] is True
    adapter.send_text.assert_awaited_once_with(
        "770408072", "Hello from the panel", reply_to=None, mirror_echo=False
    )
    mapped = (
        db_session.query(Conversation)
        .filter(
            Conversation.instance_id == instance.id,
            Conversation.platform_conversation_id == "770408072",
        )
        .first()
    )
    assert mapped is not None
    assert mapped.chatwoot_conversation_id == "70"
    assert mapped.chatwoot_contact_id == "42"
    assert mapped.chatwoot_inbox_id == "5"
    assert mapped.is_active is True


def test_extract_conversation_list_unwraps_payload_envelope():
    """Chatwoot returns {"payload": [...]} for contact conversations; the
    helper must unwrap it (and tolerate bare lists / garbage)."""
    from wootify.services.chatwoot_bridge_service import ChatwootBridgeService

    helper = ChatwootBridgeService._extract_conversation_list
    assert helper({"payload": [{"id": 1}, {"id": 2}]}) == [{"id": 1}, {"id": 2}]
    assert helper([{"id": 3}]) == [{"id": 3}]
    assert helper({"payload": None}) == []
    assert helper(None) == []
    assert helper({"payload": [{"id": 1}, "junk"]}) == [{"id": 1}]


def _mk_dedup_env(db_session, instance_key):
    """Shared fixture for outbound dedup tests."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key=instance_key,
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    adapter = AsyncMock()
    adapter.send_text = AsyncMock(
        return_value={"ok": True, "result": {"result": {"rid": 2**62 + 7}}}
    )

    runtime = MagicMock()
    runtime.platform_type = "bale_pv_enterprise"
    runtime.status = "open"
    runtime.adapter = adapter

    client = AsyncMock()
    return instance, adapter, runtime, client


def _template_payload(message_id, content="سلام، وقت بخیر 🙏"):
    return {
        "event": "message_created",
        "id": message_id,
        "message_type": "template",
        "content": content,
        "conversation": {
            "id": 70,
            "inbox_id": 5,
            "meta": {"sender": {"id": 42, "identifier": "BALE_PV:770408072"}},
            "messages": [],
        },
    }


@pytest.mark.anyio
async def test_webhook_outbound_dedup_same_chatwoot_message_id(db_session):
    """A redelivered webhook (same Chatwoot message id) must not send twice."""
    instance, adapter, runtime, client = _mk_dedup_env(db_session, "bale-pv-dedup-id")
    payload = _template_payload(9001)

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            first = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-dedup-id", payload
            )
            second = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-dedup-id", payload
            )

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["ignored"] is True
    assert second["reason"] == "duplicate_delivery"
    assert second["detail"] == "chatwoot_message_already_delivered"
    assert adapter.send_text.await_count == 1


@pytest.mark.anyio
async def test_webhook_outbound_dedup_template_content_window(db_session):
    """Double-fired greeting (identical template text, different message ids,
    milliseconds apart) is forwarded only once."""
    instance, adapter, runtime, client = _mk_dedup_env(db_session, "bale-pv-dedup-tpl")

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            first = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-dedup-tpl", _template_payload(9002)
            )
            second = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-dedup-tpl", _template_payload(9003)
            )

    assert first["ok"] is True
    assert second["ignored"] is True
    assert second["detail"] == "template_content_recently_delivered"
    assert adapter.send_text.await_count == 1


@pytest.mark.anyio
async def test_webhook_outbound_no_dedup_for_agent_messages(db_session):
    """An agent intentionally sending the same text twice must NOT be deduped
    (only template/bot messages use the content window)."""
    instance, adapter, runtime, client = _mk_dedup_env(db_session, "bale-pv-dedup-agent")
    payload_1 = _template_payload(9004)
    payload_2 = _template_payload(9005)
    payload_1["message_type"] = "outgoing"
    payload_2["message_type"] = "outgoing"

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-dedup-agent", payload_1
            )
            result = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-dedup-agent", payload_2
            )

    assert result["ok"] is True
    assert result.get("ignored") is not True
    assert adapter.send_text.await_count == 2


@pytest.mark.anyio
async def test_webhook_forwards_template_automation_message(db_session):
    """Chatwoot automation/template messages (welcome, working-hours) must be
    forwarded to the customer as if sent by the authenticated user."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-template",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token"}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    adapter = AsyncMock()
    adapter.send_text = AsyncMock(return_value={"ok": True})

    runtime = MagicMock()
    runtime.platform_type = "bale_pv_enterprise"
    runtime.status = "open"
    runtime.adapter = adapter

    client = AsyncMock()

    payload = {
        "event": "message_created",
        "message_type": "template",
        "content": "Welcome! How can we help?",
        "conversation": {
            "meta": {
                "sender": {
                    "id": 42,
                    "identifier": "BALE_PV:770408072",
                }
            },
        },
    }

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            result = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-template", payload
            )

    assert result["ok"] is True
    assert result["peer_id"] == "770408072"
    adapter.send_text.assert_awaited_once_with(
        "770408072", "Welcome! How can we help?", reply_to=None, mirror_echo=False
    )


@pytest.mark.anyio
async def test_webhook_forwards_bot_sender_message(db_session):
    """Messages sent by a Chatwoot bot (sender type agent_bot) must be forwarded
    to the customer even when message_type is incoming."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-botsender",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token"}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    adapter = AsyncMock()
    adapter.send_text = AsyncMock(return_value={"ok": True})

    runtime = MagicMock()
    runtime.platform_type = "bale_pv_enterprise"
    runtime.status = "open"
    runtime.adapter = adapter

    client = AsyncMock()

    payload = {
        "event": "message_created",
        "message_type": "incoming",
        "content": "Bot auto-reply",
        "sender": {
            "id": 99,
            "name": "Automation Bot",
            "type": "agent_bot",
        },
        "conversation": {
            "meta": {
                "sender": {
                    "id": 42,
                    "identifier": "BALE_PV:770408072",
                }
            },
        },
    }

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            result = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-botsender", payload
            )

    assert result["ok"] is True
    assert result["peer_id"] == "770408072"
    adapter.send_text.assert_awaited_once_with(
        "770408072", "Bot auto-reply", reply_to=None, mirror_echo=False
    )


@pytest.mark.anyio
async def test_webhook_ignores_incoming_customer_message(db_session):
    """Genuine incoming customer messages must not be echoed back."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-incoming",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token"}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    adapter = AsyncMock()
    runtime = MagicMock()
    runtime.platform_type = "bale_pv_enterprise"
    runtime.status = "open"
    runtime.adapter = adapter

    client = AsyncMock()

    payload = {
        "event": "message_created",
        "message_type": "incoming",
        "content": "Hello from customer",
        "conversation": {
            "meta": {
                "sender": {
                    "id": 42,
                    "identifier": "BALE_PV:770408072",
                }
            },
        },
    }

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            result = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-incoming", payload
            )

    assert result["ok"] is True
    assert result["ignored"] is True
    adapter.send_text.assert_not_awaited()


@pytest.mark.anyio
async def test_webhook_ignores_edit_reply_echo(db_session):
    """Edit replies created by the bridge must never be forwarded back to Bale."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-edit-echo",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    adapter = AsyncMock()
    runtime = MagicMock()
    runtime.platform_type = "bale_pv_enterprise"
    runtime.status = "open"
    runtime.adapter = adapter

    client = AsyncMock()

    payload = {
        "event": "message_created",
        "message_type": "outgoing",
        "source_id": "BALE_PV:888:edit",
        "content": "edited : new text",
        "conversation": {
            "meta": {
                "sender": {
                    "id": 42,
                    "identifier": "BALE_PV:770408072",
                }
            },
        },
    }

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            result = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-edit-echo", payload
            )

    assert result["ok"] is True
    assert result["ignored"] is True
    assert result["reason"] in ("platform_echo", "edit_reply_echo")
    adapter.send_text.assert_not_awaited()


@pytest.mark.anyio
async def test_webhook_propagates_message_updated_to_bale(db_session):
    """Chatwoot message_updated should be forwarded to Bale as an edit."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-edit-prop",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    conversation = Conversation(
        instance_id=instance.id,
        platform_conversation_id="770408072",
        chatwoot_conversation_id="116",
        chatwoot_contact_id="77",
        chatwoot_inbox_id="5",
        is_active=True,
    )
    db_session.add(conversation)
    db_session.commit()

    from wootify.models import MessageDirection, MessageKind, MessageMapping, MessageStatus

    # Chatwoot message ids are account-local. A different configured instance
    # may legitimately have the same message and conversation ids, so the
    # webhook lookup must remain scoped to the current Wootify instance.
    other_instance = Instance(
        instance_key="bale-pv-edit-prop-other",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 2}',
        proxy_config_encrypted="",
    )
    db_session.add(other_instance)
    db_session.flush()
    other_conversation = Conversation(
        instance_id=other_instance.id,
        platform_conversation_id="wrong-peer",
        chatwoot_conversation_id="116",
        chatwoot_contact_id="88",
        chatwoot_inbox_id="6",
        is_active=True,
    )
    db_session.add(other_conversation)
    db_session.flush()
    db_session.add(
        MessageMapping(
            conversation_id=str(other_conversation.id),
            direction=MessageDirection.platform_to_chatwoot,
            message_kind=MessageKind.text,
            platform_message_id="wrong-message",
            chatwoot_message_id="999",
            status=MessageStatus.sent,
            platform_payload_json={"text": "not this account"},
        )
    )
    db_session.commit()

    mapping = MessageMapping(
        conversation_id=str(conversation.id),
        direction=MessageDirection.chatwoot_to_platform,
        message_kind=MessageKind.text,
        platform_message_id="888",
        chatwoot_message_id="999",
        status=MessageStatus.sent,
        platform_payload_json={"text": "old text"},
    )
    db_session.add(mapping)
    db_session.commit()

    adapter = AsyncMock()
    adapter.edit_message = AsyncMock(return_value={"ok": True})
    runtime = MagicMock()
    runtime.platform_type = "bale_pv_enterprise"
    runtime.status = "open"
    runtime.adapter = adapter

    client = AsyncMock()

    payload = {
        "event": "message_updated",
        "id": 999,
        "content": "new edited text",
        "conversation": {"id": 116},
    }

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            result = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-edit-prop", payload
            )

    assert result["ok"] is True
    assert result["status"] == "edit_propagated"
    adapter.edit_message.assert_awaited_once_with(
        peer_id="770408072",
        message_id="888",
        text="new edited text",
    )

    db_session.refresh(mapping)
    assert mapping.platform_payload_json == {"text": "new edited text"}


@pytest.mark.anyio
async def test_webhook_does_not_edit_authenticated_users_bale_message(db_session):
    """An edit webhook must not modify an inbound Bale user's message."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()
    instance = Instance(
        instance_key="bale-pv-inbound-edit-skip",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.flush()
    conversation = Conversation(
        instance_id=instance.id,
        platform_conversation_id="770408072",
        chatwoot_conversation_id="116",
        chatwoot_contact_id="77",
        chatwoot_inbox_id="5",
        is_active=True,
    )
    db_session.add(conversation)
    db_session.flush()

    from wootify.models import MessageDirection, MessageKind, MessageMapping, MessageStatus

    db_session.add(
        MessageMapping(
            conversation_id=str(conversation.id),
            direction=MessageDirection.platform_to_chatwoot,
            message_kind=MessageKind.text,
            platform_message_id="888",
            chatwoot_message_id="999",
            status=MessageStatus.sent,
            platform_payload_json={"text": "the Bale user's text"},
        )
    )
    db_session.commit()

    adapter = AsyncMock()
    runtime = MagicMock(status="open", adapter=adapter)
    client = AsyncMock()
    payload = {
        "event": "message_updated",
        "id": 999,
        "content": "must not change Bale user's message",
        "conversation": {"id": 116},
    }
    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            result = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, instance.instance_key, payload
            )

    assert result == {
        "ok": True,
        "ignored": True,
        "reason": "not_chatwoot_outbound",
        "detail": "not_chatwoot_outbound",
    }
    adapter.edit_message.assert_not_awaited()


@pytest.mark.anyio
async def test_webhook_skips_message_updated_when_content_unchanged(db_session):
    """message_updated with the same content must not be forwarded."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-edit-skip",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    conversation = Conversation(
        instance_id=instance.id,
        platform_conversation_id="770408072",
        chatwoot_conversation_id="116",
        chatwoot_contact_id="77",
        chatwoot_inbox_id="5",
        is_active=True,
    )
    db_session.add(conversation)
    db_session.commit()

    from wootify.models import MessageDirection, MessageKind, MessageMapping, MessageStatus

    mapping = MessageMapping(
        conversation_id=str(conversation.id),
        direction=MessageDirection.chatwoot_to_platform,
        message_kind=MessageKind.text,
        platform_message_id="888",
        chatwoot_message_id="999",
        status=MessageStatus.sent,
        platform_payload_json={"text": "same text"},
    )
    db_session.add(mapping)
    db_session.commit()

    adapter = AsyncMock()
    adapter.edit_message = AsyncMock()
    runtime = MagicMock()
    runtime.platform_type = "bale_pv_enterprise"
    runtime.status = "open"
    runtime.adapter = adapter

    client = AsyncMock()

    payload = {
        "event": "message_updated",
        "id": 999,
        "content": "same text",
        "conversation": {"id": 116},
    }

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            result = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-edit-skip", payload
            )

    assert result["ok"] is True
    assert result["ignored"] is True
    assert result["reason"] == "content_unchanged"
    adapter.edit_message.assert_not_awaited()


@pytest.mark.anyio
async def test_webhook_skips_message_updated_for_edit_reply(db_session):
    """Edits to bridge-created edit replies must not be forwarded."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-edit-reply",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    conversation = Conversation(
        instance_id=instance.id,
        platform_conversation_id="770408072",
        chatwoot_conversation_id="116",
        chatwoot_contact_id="77",
        chatwoot_inbox_id="5",
        is_active=True,
    )
    db_session.add(conversation)
    db_session.commit()

    from wootify.models import MessageDirection, MessageKind, MessageMapping, MessageStatus

    mapping = MessageMapping(
        conversation_id=str(conversation.id),
        direction=MessageDirection.chatwoot_to_platform,
        message_kind=MessageKind.text,
        platform_message_id="888:edit:123",
        chatwoot_message_id="1000",
        status=MessageStatus.sent,
        platform_payload_json={"text": "edited : old", "edit_reply": True},
    )
    db_session.add(mapping)
    db_session.commit()

    adapter = AsyncMock()
    adapter.edit_message = AsyncMock()
    runtime = MagicMock()
    runtime.platform_type = "bale_pv_enterprise"
    runtime.status = "open"
    runtime.adapter = adapter

    client = AsyncMock()

    payload = {
        "event": "message_updated",
        "id": 1000,
        "content": "edited : new",
        "conversation": {"id": 116},
    }

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            result = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-edit-reply", payload
            )

    assert result["ok"] is True
    assert result["ignored"] is True
    assert result["reason"] == "edit_reply"
    adapter.edit_message.assert_not_awaited()


@pytest.mark.anyio
async def test_webhook_propagates_message_updated_deleted(db_session):
    """Deleted messages (content_attributes.deleted) propagate to the platform."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-edit-del",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    conversation = Conversation(
        instance_id=instance.id,
        platform_conversation_id="770408072",
        chatwoot_conversation_id="116",
        chatwoot_contact_id="77",
        chatwoot_inbox_id="5",
        is_active=True,
    )
    db_session.add(conversation)
    db_session.commit()

    from wootify.models import MessageDirection, MessageKind, MessageMapping, MessageStatus

    mapping = MessageMapping(
        conversation_id=str(conversation.id),
        direction=MessageDirection.platform_to_chatwoot,
        message_kind=MessageKind.text,
        platform_message_id="888",
        chatwoot_message_id="999",
        status=MessageStatus.sent,
        platform_payload_json={"text": "old text"},
    )
    db_session.add(mapping)
    db_session.commit()

    adapter = AsyncMock()
    adapter.edit_message = AsyncMock()
    adapter.delete_message = AsyncMock(return_value={"ok": True, "result": {}})
    runtime = MagicMock()
    runtime.platform_type = "bale_pv_enterprise"
    runtime.status = "open"
    runtime.adapter = adapter

    client = AsyncMock()

    payload = {
        "event": "message_updated",
        "id": 999,
        "content": "new text",
        "content_attributes": {"deleted": True},
        "conversation": {"id": 116},
    }

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            result = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-edit-del", payload
            )

    assert result["ok"] is True
    assert result["status"] == "delete_propagated"
    assert result["platform_message_id"] == "888"
    adapter.edit_message.assert_not_awaited()
    adapter.delete_message.assert_awaited_once_with(
        peer_id="770408072",
        message_id="888",
    )


@pytest.mark.anyio
async def test_ingest_recovers_from_missing_conversation(db_session):
    """If posting to a mapped conversation returns 404, create a new one and retry."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-recover",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token", "inbox_id": 5}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    contact_id = 77
    old_conv_id = 116
    new_conv_id = 999

    # Pre-seed an active local conversation pointing to the missing remote one.
    db_session.add(
        Conversation(
            instance_id=instance.id,
            platform_conversation_id="770408072",
            chatwoot_conversation_id=str(old_conv_id),
            chatwoot_contact_id=str(contact_id),
            chatwoot_inbox_id="5",
            is_active=True,
        )
    )
    db_session.commit()

    client = AsyncMock()
    # First post fails with 404; retry succeeds.
    not_found = httpx.Response(404, json={"error": "Resource could not be found"})
    client.post_message = AsyncMock(
        side_effect=[
            httpx.HTTPStatusError("404", request=MagicMock(), response=not_found),
            {"id": 12345},
        ]
    )
    client.list_contact_conversations = AsyncMock(return_value=[])
    client.create_conversation = AsyncMock(return_value={"id": new_conv_id})
    client.search_contacts = AsyncMock(return_value={"payload": [{"id": contact_id}]})

    event = {
        "chat_id": "770408072",
        "chat_type": "private",
        "from_name": "Bale User 770408072",
        "text": "hello",
        "message_id": "111",
        "platform_message_id": "111",
        "outgoing": False,
    }

    with patch.object(
        chatwoot_bridge,
        "_chatwoot_client_for_instance",
        return_value=(instance, {"account_id": 1, "inbox_id": 5}, client),
    ):
        result = await chatwoot_bridge.ingest_platform_event(
            db_session, "bale-pv-recover", event
        )

    assert result["ok"] is True
    assert result["chatwoot_conversation_id"] == new_conv_id
    assert result["chatwoot_message_id"] == 12345
    assert client.create_conversation.await_count == 1
    assert client.post_message.await_count == 2

    # The local mapping row should be updated to the new conversation id and remain active.
    conv = (
        db_session.query(Conversation)
        .filter_by(instance_id=instance.id, platform_conversation_id="770408072")
        .first()
    )
    assert conv is not None
    assert conv.chatwoot_conversation_id == str(new_conv_id)
    assert conv.is_active is True


@pytest.mark.anyio
async def test_webhook_marks_conversation_resolved(db_session):
    """A conversation_status_changed event should mark the local mapping inactive."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-status",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token"}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    db_session.add(
        Conversation(
            instance_id=instance.id,
            platform_conversation_id="770408072",
            chatwoot_conversation_id="116",
            chatwoot_contact_id="77",
            chatwoot_inbox_id="5",
            is_active=True,
        )
    )
    db_session.commit()

    client = AsyncMock()
    runtime = MagicMock()
    runtime.status = "open"
    payload = {
        "event": "conversation_status_changed",
        "conversation": {"id": 116, "status": "resolved"},
    }

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            result = await chatwoot_bridge.handle_chatwoot_webhook(
                db_session, "bale-pv-status", payload
            )

    assert result["ok"] is True
    assert result["status"] == "marked_inactive"

    conv = (
        db_session.query(Conversation)
        .filter_by(instance_id=instance.id, chatwoot_conversation_id="116")
        .first()
    )
    assert conv is not None
    assert conv.is_active is False


@pytest.mark.anyio
async def test_resolve_attachments_converts_webp_sticker_to_jpeg():
    """Inbound WEBP stickers are converted to JPEG so Chatwoot can display them."""
    from PIL import Image
    from io import BytesIO

    adapter = BalePvAdapter("test", {})
    attachments = [
        {
            "file_id": '{"file_id":123,"access_hash":456,"peer_id":789}',
            "filename": "sticker.webp",
            "content_type": "image/webp",
        }
    ]

    # Generate a real WEBP image so Pillow can decode it.
    img = Image.new("RGBA", (64, 64), color=(0, 128, 255, 255))
    buf = BytesIO()
    img.save(buf, format="WEBP")
    webp_bytes = buf.getvalue()

    with patch(
        "wootify.adapters.bale_pv.bale_pv.download_file_by_id",
        new=AsyncMock(return_value=(webp_bytes, "image/webp", "/tmp/sticker.webp")),
    ):
        resolved = await adapter.resolve_attachments(attachments)

    assert len(resolved) == 1
    assert resolved[0]["filename"] == "sticker.jpg"
    assert resolved[0]["content_type"] == "image/jpeg"
    assert resolved[0]["content"].startswith(b"\xff\xd8")
    # Verify the output is a readable JPEG.
    jpeg_img = Image.open(BytesIO(resolved[0]["content"]))
    assert jpeg_img.format == "JPEG"
    assert jpeg_img.size == (64, 64)


@pytest.mark.anyio
async def test_resolve_attachments_normalizes_jpeg_named_as_png():
    """Bale sometimes sends JPEG stickers named *.png; we fix the extension."""
    from PIL import Image
    from io import BytesIO

    adapter = BalePvAdapter("test", {})
    attachments = [
        {
            "file_id": '{"file_id":123,"access_hash":456,"peer_id":789}',
            "filename": "sticker.png",
            "content_type": "image/jpeg",
        }
    ]

    img = Image.new("RGB", (64, 64), color=(255, 0, 0))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()

    with patch(
        "wootify.adapters.bale_pv.bale_pv.download_file_by_id",
        new=AsyncMock(return_value=(jpeg_bytes, "image/jpeg", "/tmp/sticker.png")),
    ):
        resolved = await adapter.resolve_attachments(attachments)

    assert len(resolved) == 1
    assert resolved[0]["filename"] == "sticker.jpg"
    assert resolved[0]["content_type"] == "image/jpeg"
    assert resolved[0]["content"].startswith(b"\xff\xd8")


def test_extract_chatwoot_attachments_direct_payload():
    payload = {
        "event": "message_created",
        "attachments": [
            {"file_type": "image", "data_url": "/rails/active_storage/blobs/photo.png"}
        ],
    }
    atts = ChatwootBridgeService._extract_chatwoot_attachments(payload)
    assert len(atts) == 1
    assert atts[0]["data_url"].endswith("photo.png")


def test_extract_chatwoot_attachments_nested_message():
    payload = {
        "event": "message_created",
        "message": {
            "attachments": [
                {"file_type": "video", "data_url": "/rails/active_storage/blobs/video.mp4"}
            ]
        },
    }
    atts = ChatwootBridgeService._extract_chatwoot_attachments(payload)
    assert len(atts) == 1
    assert atts[0]["data_url"].endswith("video.mp4")


def test_extract_chatwoot_attachments_legacy_conversation():
    payload = {
        "event": "message_created",
        "conversation": {
            "messages": [
                {
                    "attachments": [
                        {"file_type": "file", "data_url": "/rails/active_storage/blobs/doc.pdf"}
                    ]
                }
            ]
        },
    }
    atts = ChatwootBridgeService._extract_chatwoot_attachments(payload)
    assert len(atts) == 1
    assert atts[0]["data_url"].endswith("doc.pdf")


def test_send_type_for_filename_maps_mime_types():
    from bale_pv_connector.messaging_messages import SendTypeValue

    st = BalePvConnector._send_type_for_filename
    assert st("photo.jpg", "image/jpeg") == SendTypeValue.SEND_TYPE_PHOTO
    assert st("anim.gif", "image/gif") == SendTypeValue.SEND_TYPE_GIF
    assert st("sticker.webp", "image/webp") == SendTypeValue.SEND_TYPE_STICKER
    assert st("clip.mp4", "video/mp4") == SendTypeValue.SEND_TYPE_VIDEO
    assert st("voice.ogg", "audio/ogg") == SendTypeValue.SEND_TYPE_VOICE
    assert st("song.mp3", "audio/mpeg") == SendTypeValue.SEND_TYPE_AUDIO
    assert st("report.pdf", "application/pdf") == SendTypeValue.SEND_TYPE_DOCUMENT


def test_media_metadata_for_images_generates_thumb_and_ext():
    from PIL import Image
    from io import BytesIO
    from bale_pv_connector.messaging_messages import FastThumb, ImageExt

    img = Image.new("RGB", (200, 100), color="red")
    buf = BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    connector = BalePvConnector()
    thumb, ext = connector._media_metadata_for_send(
        filename="photo.png",
        mime_type="image/png",
        file_bytes=png_bytes,
        send_type=0,
    )

    assert isinstance(thumb, FastThumb)
    assert thumb.width == 200
    assert thumb.height == 100
    assert thumb.thumb.startswith(b"\xff\xd8")  # JPEG thumbnail
    assert isinstance(ext, ImageExt)
    assert ext.width == 200
    assert ext.height == 100


def test_media_metadata_for_audio_returns_audio_ext():
    from bale_pv_connector.messaging_messages import AudioExt

    connector = BalePvConnector()
    thumb, ext = connector._media_metadata_for_send(
        filename="voice.ogg",
        mime_type="audio/ogg",
        file_bytes=b"OggS" + b"\x00" * 20,
        send_type=0,
    )
    assert thumb is None
    assert isinstance(ext, AudioExt)


def test_document_message_serializes_thumb_and_ext():
    from bale_pv_connector.messaging_messages import (
        DocumentMessage, FastThumb, ImageExt, TextMessage
    )
    from bale_pv_connector.protobuf_wire import ProtobufParser

    doc = DocumentMessage(
        file_id=123,
        access_hash=456,
        file_size=789,
        name="photo.png",
        mime_type="image/png",
        caption="hi",
        thumb=FastThumb(width=10, height=10, thumb=b"thumbbytes"),
        ext=ImageExt(width=100, height=50),
    )
    serialized = doc.serialize()
    fields = ProtobufParser(serialized).parse()
    assert fields[1] == [123]
    assert fields[2] == [456]
    assert fields[3] == [789]
    assert isinstance(fields[4][0], bytes)
    assert isinstance(fields[5][0], bytes)
    assert isinstance(fields[6][0], bytes)  # thumb
    assert isinstance(fields[7][0], bytes)  # ext
    assert isinstance(fields[8][0], bytes)  # caption


def test_extract_attachment_refs_extracts_sticker():
    adapter = BalePvAdapter("test", {})
    message = {
        "sticker": {
            "file_id": '{"file_id":123,"access_hash":456,"peer_id":789}',
            "mime_type": "image/webp",
        }
    }
    refs = adapter._extract_attachment_refs(message)
    assert len(refs) == 1
    assert refs[0]["filename"] == "sticker.webp"
    assert refs[0]["content_type"] == "image/webp"


def test_normalize_incoming_update_extracts_sticker():
    """A raw update carrying a sticker should produce an event with attachments."""
    adapter = BalePvAdapter("test", {"bale_pv_phone_number": "989136421196"})
    raw = {
        "update_id": 123,
        "message": {
            "message_id": "456",
            "date": 1,
            "chat": {"id": "1755271951", "type": "private"},
            "from": {"id": 1755271951, "first_name": "Amin"},
            "text": "",
            "sticker": {
                "file_id": '{"file_id":123,"access_hash":456,"peer_id":789}',
                "mime_type": "image/webp",
            },
        },
    }
    event = adapter.normalize_incoming_update(raw)
    assert event is not None
    assert len(event["attachments"]) == 1
    assert event["attachments"][0]["filename"] == "sticker.webp"
    assert event["attachments"][0]["content_type"] == "image/webp"


def test_normalize_filename_extension_fixes_mismatched_sticker():
    adapter = BalePvAdapter("test", {})
    assert adapter._normalize_filename_extension("sticker.png", "image/jpeg") == "sticker.jpg"
    assert adapter._normalize_filename_extension("sticker.webp", "image/jpeg") == "sticker.jpg"
    assert adapter._normalize_filename_extension("photo", "image/png") == "photo.png"
    assert adapter._normalize_filename_extension("sticker.jpg", "image/jpeg") == "sticker.jpg"
    assert adapter._normalize_filename_extension("file.pdf", "application/pdf") == "file.pdf"


def test_normalize_content_type_prefers_magic_bytes_over_declared_type():
    """Bale sometimes sends JPEG bytes named *.png with image/jpeg MIME."""
    adapter = BalePvAdapter("test", {})
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF"
    # Declared type and filename say PNG, but magic bytes are JPEG.
    ct = adapter._normalize_content_type(
        filename="sticker.png",
        content_type="image/png",
        content=jpeg_bytes,
    )
    assert ct == "image/jpeg"


def test_normalize_content_type_prefers_magic_bytes_over_filename():
    adapter = BalePvAdapter("test", {})
    png_bytes = b"\x89PNG\r\n\x1a\n"
    ct = adapter._normalize_content_type(
        filename="sticker.jpg",
        content_type=None,
        content=png_bytes,
    )
    assert ct == "image/png"


def test_normalize_content_type_falls_back_to_declared_type():
    adapter = BalePvAdapter("test", {})
    ct = adapter._normalize_content_type(
        filename="report.txt",
        content_type="text/plain",
        content=b"hello",
    )
    assert ct == "text/plain"


def test_unique_attachment_filename_preserves_extension():
    name = ChatwootBridgeService._unique_attachment_filename("sticker.webp")
    assert name.endswith(".webp")
    assert name.startswith("sticker_")
    assert len(name) > len("sticker.webp")


def test_unique_attachment_filename_generates_distinct_names():
    names = {
        ChatwootBridgeService._unique_attachment_filename("sticker.webp")
        for _ in range(50)
    }
    assert len(names) == 50


def test_unique_attachment_filename_handles_missing_extension():
    name = ChatwootBridgeService._unique_attachment_filename("file")
    assert name.startswith("file_")
    assert "." not in name


@pytest.mark.anyio
async def test_post_message_to_chatwoot_uses_unique_filenames(db_session):
    """Attachments posted to Chatwoot should have unique filenames."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-unique-names",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token", "inbox_id": 5}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    client = AsyncMock()
    client.post_message_with_attachments = AsyncMock(return_value={"id": 42})

    attachments = [
        {"filename": "sticker.webp", "content": b"RIFF\x00\x00\x00\x00WEBP", "content_type": "image/webp"},
        {"filename": "sticker.webp", "content": b"RIFF\x00\x00\x00\x00WEBP", "content_type": "image/webp"},
    ]

    await chatwoot_bridge._post_message_to_chatwoot(
        client, 1, 5, {"content": "hi"}, attachments
    )

    assert client.post_message_with_attachments.await_count == 1
    call_args = client.post_message_with_attachments.await_args
    files = call_args.args[3]
    assert len(files) == 2
    first, second = files[0][0], files[1][0]
    assert first != second
    assert first.endswith(".webp") and second.endswith(".webp")


@pytest.mark.anyio
async def test_persist_mapping_skips_duplicate_platform_message_id(db_session):
    """Re-ingesting the same platform message must not raise a UNIQUE error."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-dup",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token"}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    conversation = Conversation(
        instance_id=instance.id,
        platform_conversation_id="770408072",
        chatwoot_conversation_id="100",
        chatwoot_contact_id="77",
        chatwoot_inbox_id="5",
        is_active=True,
    )
    db_session.add(conversation)
    db_session.commit()

    from wootify.models import MessageDirection, MessageKind, MessageStatus

    # First persist should create a row.
    result1 = chatwoot_bridge._persist_mapping(
        db_session,
        instance=instance,
        conversation_id=str(conversation.id),
        direction=MessageDirection.platform_to_chatwoot,
        message_kind=MessageKind.text,
        chatwoot_message_id="200",
        platform_message_id="13190079574515427814",
        status=MessageStatus.sent,
    )
    assert result1 is not None
    assert result1.platform_message_id == "13190079574515427814"

    # Second persist with the same platform_message_id should be idempotent.
    result2 = chatwoot_bridge._persist_mapping(
        db_session,
        instance=instance,
        conversation_id=str(conversation.id),
        direction=MessageDirection.platform_to_chatwoot,
        message_kind=MessageKind.text,
        chatwoot_message_id="200",
        platform_message_id="13190079574515427814",
        status=MessageStatus.sent,
    )
    assert result2 is not None
    assert result2.id == result1.id

    # Only one mapping row should exist.
    from wootify.models import MessageMapping
    rows = db_session.query(MessageMapping).filter_by(
        conversation_id=str(conversation.id),
        platform_message_id="13190079574515427814",
    ).all()
    assert len(rows) == 1


@pytest.mark.anyio
async def test_persist_mapping_updates_existing_unsent_row(db_session):
    """An existing mapping that is not yet sent should be updated."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-update",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token"}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    conversation = Conversation(
        instance_id=instance.id,
        platform_conversation_id="770408072",
        chatwoot_conversation_id="100",
        chatwoot_contact_id="77",
        chatwoot_inbox_id="5",
        is_active=True,
    )
    db_session.add(conversation)
    db_session.commit()

    from wootify.models import MessageDirection, MessageKind, MessageMapping, MessageStatus

    existing = MessageMapping(
        conversation_id=str(conversation.id),
        direction=MessageDirection.platform_to_chatwoot,
        message_kind=MessageKind.text,
        platform_message_id="999",
        status=MessageStatus.pending,
    )
    db_session.add(existing)
    db_session.commit()

    result = chatwoot_bridge._persist_mapping(
        db_session,
        instance=instance,
        conversation_id=str(conversation.id),
        direction=MessageDirection.platform_to_chatwoot,
        message_kind=MessageKind.media,
        chatwoot_message_id="300",
        platform_message_id="999",
        status=MessageStatus.sent,
    )
    assert result is not None
    assert result.id == existing.id
    assert result.chatwoot_message_id == "300"
    assert result.message_kind == MessageKind.media
    assert result.status == MessageStatus.sent


@pytest.mark.anyio
async def test_ingest_skips_duplicate_platform_message_before_posting(db_session):
    """A re-processed platform message must not be posted to Chatwoot again."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-dup-post",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token", "inbox_id": 5}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    contact_id = 77
    conv_id = 116

    conversation = Conversation(
        instance_id=instance.id,
        platform_conversation_id="770408072",
        chatwoot_conversation_id=str(conv_id),
        chatwoot_contact_id=str(contact_id),
        chatwoot_inbox_id="5",
        is_active=True,
    )
    db_session.add(conversation)
    db_session.commit()

    from wootify.models import MessageDirection, MessageKind, MessageMapping, MessageStatus

    existing = MessageMapping(
        conversation_id=str(conversation.id),
        direction=MessageDirection.platform_to_chatwoot,
        message_kind=MessageKind.media,
        platform_message_id="888",
        chatwoot_message_id="999",
        status=MessageStatus.sent,
    )
    db_session.add(existing)
    db_session.commit()

    client = AsyncMock()
    client.post_message = AsyncMock(return_value={"id": 12345})
    client.post_message_with_attachments = AsyncMock(return_value={"id": 12345})
    client.search_contacts = AsyncMock(return_value={"payload": [{"id": contact_id}]})

    event = {
        "chat_id": "770408072",
        "chat_type": "private",
        "from_name": "Bale User 770408072",
        "text": "",
        "message_id": "888",
        "platform_message_id": "888",
        "outgoing": False,
        "attachments": [
            {"filename": "sticker.webp", "content": b"RIFF\x00\x00\x00\x00WEBP", "content_type": "image/webp"}
        ],
    }

    with patch.object(
        chatwoot_bridge,
        "_chatwoot_client_for_instance",
        return_value=(instance, {"account_id": 1, "inbox_id": 5}, client),
    ):
        result = await chatwoot_bridge.ingest_platform_event(
            db_session, "bale-pv-dup-post", event
        )

    assert result["ok"] is True
    assert result.get("duplicate") is True
    assert result["chatwoot_message_id"] == "999"
    client.post_message.assert_not_awaited()
    client.post_message_with_attachments.assert_not_awaited()


@pytest.mark.anyio
async def test_ingest_posts_edit_as_reply_when_text_changes(db_session):
    """An inbound message edit must be posted as a reply to the original."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-edit",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token", "inbox_id": 5}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    contact_id = 77
    conv_id = 116

    conversation = Conversation(
        instance_id=instance.id,
        platform_conversation_id="770408072",
        chatwoot_conversation_id=str(conv_id),
        chatwoot_contact_id=str(contact_id),
        chatwoot_inbox_id="5",
        is_active=True,
    )
    db_session.add(conversation)
    db_session.commit()

    from wootify.models import MessageDirection, MessageKind, MessageMapping, MessageStatus

    existing = MessageMapping(
        conversation_id=str(conversation.id),
        direction=MessageDirection.platform_to_chatwoot,
        message_kind=MessageKind.text,
        platform_message_id="888",
        chatwoot_message_id="999",
        status=MessageStatus.sent,
        platform_payload_json={"text": "original text"},
    )
    db_session.add(existing)
    db_session.commit()

    client = AsyncMock()
    client.post_message = AsyncMock(return_value={"id": 1111})
    client.post_message_with_attachments = AsyncMock(return_value={"id": 1111})
    client.search_contacts = AsyncMock(return_value={"payload": [{"id": contact_id}]})

    event = {
        "chat_id": "770408072",
        "chat_type": "private",
        "from_name": "Bale User 770408072",
        "text": "edited text",
        "message_id": "888",
        "platform_message_id": "888",
        "outgoing": False,
        "attachments": [],
    }

    with patch.object(
        chatwoot_bridge,
        "_chatwoot_client_for_instance",
        return_value=(instance, {"account_id": 1, "inbox_id": 5}, client),
    ):
        result = await chatwoot_bridge.ingest_platform_event(
            db_session, "bale-pv-edit", event
        )

    assert result["ok"] is True
    assert result.get("status") == "edit_replied"
    assert result["original_chatwoot_message_id"] == "999"
    assert result["edited_chatwoot_message_id"] == 1111

    client.post_message.assert_awaited_once()
    call_args = client.post_message.await_args
    assert call_args.args[0] == 1  # account_id
    assert call_args.args[1] == conv_id  # conversation_id
    payload = call_args.args[2]
    assert payload["content"] == "edited : edited text"
    assert payload["message_type"] == "incoming"
    assert payload["content_attributes"]["in_reply_to"] == 999

    db_session.refresh(existing)
    assert existing.platform_payload_json == {"text": "edited text"}


@pytest.mark.anyio
async def test_post_message_with_attachments_does_not_retry_on_timeout():
    """Media uploads that time out must not be retried to avoid duplicates."""
    from wootify.clients.chatwoot_client import ChatwootClient

    client = ChatwootClient(base_url="http://chatwoot", token="token", timeout=1)

    attempts = 0
    async def fake_request(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("timed out", request=MagicMock())

    with patch.object(client._client, "request", new=fake_request):
        with pytest.raises(httpx.ReadTimeout):
            await client.post_message_with_attachments(
                1,
                5,
                {"content": "hi"},
                [("sticker.webp", b"RIFF\x00\x00\x00\x00WEBP", "image/webp")],
            )

    assert attempts == 1, "ReadTimeout should not be retried for media uploads"


@pytest.mark.anyio
async def test_post_message_does_not_retry_on_timeout():
    """Text message posts that time out must not be retried to avoid duplicates."""
    from wootify.clients.chatwoot_client import ChatwootClient

    client = ChatwootClient(base_url="http://chatwoot", token="token", timeout=1)

    attempts = 0
    async def fake_request(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("timed out", request=MagicMock())

    with patch.object(client._client, "request", new=fake_request):
        with pytest.raises(httpx.ReadTimeout):
            await client.post_message(1, 5, {"content": "hi"})

    assert attempts == 1, "ReadTimeout should not be retried for text messages"


@pytest.mark.anyio
async def test_post_message_retries_connect_error_before_chatwoot_receives_request():
    """DNS/connection failures are safe to retry for outbound messages."""
    from wootify.clients.chatwoot_client import ChatwootClient

    client = ChatwootClient(base_url="http://chatwoot", token="token", timeout=1)
    attempts = 0

    async def fake_request(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("name lookup failed", request=MagicMock())
        return httpx.Response(
            200,
            json={"id": 42},
            request=httpx.Request("POST", "http://chatwoot/api/v1/accounts/1/conversations/5/messages"),
        )

    with patch.object(client._client, "request", new=fake_request), patch(
        "asyncio.sleep", new=AsyncMock()
    ):
        result = await client.post_message(1, 5, {"content": "hi"})

    assert attempts == 3
    assert result == {"id": 42}


@pytest.mark.anyio
async def test_get_request_still_retries_on_timeout():
    """GET requests should still retry on ReadTimeout."""
    from wootify.clients.chatwoot_client import ChatwootClient

    client = ChatwootClient(base_url="http://chatwoot", token="token", timeout=1)

    attempts = 0
    async def fake_request(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("timed out", request=MagicMock())

    with patch.object(client._client, "request", new=fake_request):
        with pytest.raises(httpx.ReadTimeout):
            await client.list_contact_conversations(1, 5)

    assert attempts == 3, "GET requests should still retry on ReadTimeout"


def _build_load_groups_response_body(groups):
    """Build a synthetic LoadGroups response body (field 1 = repeated Group)."""
    from bale_pv_connector.protobuf_wire import ProtobufMessage
    body = ProtobufMessage()
    for gid, title, access_hash in groups:
        group = ProtobufMessage()
        group.add_int32(1, gid)
        if access_hash is not None:
            group.add_int64(2, access_hash)
        info = ProtobufMessage()
        info.add_string(1, title)
        group.add_message(17, info)
        body.add_message(1, group)
    return body.serialize()


def test_parse_load_groups_response():
    """LoadGroups parser extracts group ids, titles, and access hashes."""
    from bale_pv_connector.dialog_parser import parse_load_groups_response
    body = _build_load_groups_response_body([
        (1030275959, "beresha_shoes", 12345),
        (178680737, "novinmed", None),
    ])
    parsed = parse_load_groups_response(body)
    groups = {g["id"]: g for g in parsed["groups"]}
    assert len(groups) == 2
    assert groups[1030275959]["title"] == "beresha_shoes"
    assert groups[1030275959]["access_hash"] == 12345
    assert groups[178680737]["title"] == "novinmed"
    assert groups[178680737]["access_hash"] is None


@pytest.mark.anyio
async def test_resolve_group_title_uses_load_groups():
    """resolve_group_title should call LoadGroups and cache the title."""
    connector = BalePvConnector()
    runtime = BalePvInstanceRuntime(
        instance_key="test-instance",
        phone_number="989123456789",
    )
    runtime.auth_state = "authenticated"
    connector._instances["test-instance"] = runtime

    client = AsyncMock()
    client.load_groups = AsyncMock(return_value=_build_load_groups_response_body([
        (395054013, "testprivategroup", 999),
    ]))
    runtime.client = client

    title = await connector.resolve_group_title("test-instance", 395054013, 2)
    assert title == "testprivategroup"
    assert runtime.chat_title_cache[395054013] == "(group) testprivategroup"
    assert runtime.group_access_hash_cache[395054013] == 999
    client.load_groups.assert_awaited_once()


@pytest.mark.anyio
async def test_resolve_group_title_returns_cached_title():
    """A cached title should be returned without calling LoadGroups."""
    connector = BalePvConnector()
    runtime = BalePvInstanceRuntime(
        instance_key="test-instance",
        phone_number="989123456789",
    )
    runtime.auth_state = "authenticated"
    runtime.chat_title_cache[395054013] = "(group) cached_group"
    connector._instances["test-instance"] = runtime
    runtime.client = AsyncMock()

    title = await connector.resolve_group_title("test-instance", 395054013, 2)
    assert title == "cached_group"
    runtime.client.load_groups.assert_not_awaited()


# ---------------------------------------------------------------------------
# Service notices ("<name> joined Bale")
# ---------------------------------------------------------------------------


def _build_service_notice_frame(peer_id: int, sender_uid: int, rid: int) -> bytes:
    """Build a WS update frame whose Message G has no displayable content.

    Simulates Bale's ServiceMessage bodies (e.g. the "<name> joined Bale"
    contact-registered notice): no text/document/sticker fields.
    """
    from bale_pv_connector.messaging_messages import Peer
    from bale_pv_connector.protobuf_wire import ProtobufMessage
    from bale_pv_connector.update_parser import BaleUpdateType

    service_body = ProtobufMessage()
    service_body.add_int64(1, sender_uid)  # opaque service payload
    msg = ProtobufMessage()
    msg.add_bytes(2, service_body.serialize())  # unknown content field in Message G

    update = ProtobufMessage()
    update.add_bytes(1, Peer(peer_id).serialize())
    update.add_int32(2, sender_uid)
    update.add_int64(4, rid)
    update.add_bytes(5, msg.serialize())

    wrapper = ProtobufMessage()
    wrapper.add_bytes(BaleUpdateType.NEW_MESSAGE, update.serialize())

    inner = ProtobufMessage()
    inner.add_bytes(1, wrapper.serialize())

    outer = ProtobufMessage()
    outer.add_bytes(1, inner.serialize())
    return outer.serialize()


def test_parse_raw_update_flags_service_notice():
    """Content-less private messages are tagged as service notices."""
    frame = _build_service_notice_frame(peer_id=456, sender_uid=456, rid=999)
    parsed = BalePvConnector._parse_raw_update(
        frame, user_cache={456: "Sara"}, self_user_id=999
    )
    assert parsed is not None
    message = parsed["message"]
    assert message["_service_notice"] is True
    assert message["text"] == ""
    assert message["chat"]["id"] == "456"
    # The contact name from the user cache is still used.
    assert message["from"]["first_name"] == "Sara"


def test_parse_raw_update_text_message_not_flagged():
    """Normal text messages must not be flagged as service notices."""
    from bale_pv_connector.messaging_messages import Peer, TextMessage
    from bale_pv_connector.protobuf_wire import ProtobufMessage
    from bale_pv_connector.update_parser import BaleUpdateType

    msg = ProtobufMessage()
    msg.add_message(15, TextMessage("hello"))
    update = ProtobufMessage()
    update.add_bytes(1, Peer(456).serialize())
    update.add_int32(2, 456)
    update.add_int64(4, 1000)
    update.add_bytes(5, msg.serialize())
    wrapper = ProtobufMessage()
    wrapper.add_bytes(BaleUpdateType.NEW_MESSAGE, update.serialize())
    inner = ProtobufMessage()
    inner.add_bytes(1, wrapper.serialize())
    outer = ProtobufMessage()
    outer.add_bytes(1, inner.serialize())

    parsed = BalePvConnector._parse_raw_update(outer.serialize(), user_cache={}, self_user_id=999)
    assert parsed is not None
    assert "_service_notice" not in parsed["message"]
    assert parsed["message"]["text"] == "hello"


def test_adapter_normalize_marks_service_notice():
    adapter = BalePvAdapter("test", {"bale_pv_phone_number": "989136421196"})
    raw = {
        "update_id": 126,
        "message": {
            "message_id": "999",
            "date": 1,
            "chat": {"id": "456", "type": "private", "title": "Sara"},
            "from": {"id": 456, "first_name": "Sara"},
            "text": "",
            "_service_notice": True,
        },
    }
    event = adapter.normalize_incoming_update(raw)
    assert event is not None
    assert event["service_notice"] is True
    assert event["from_name"] == "Sara"
    assert event["outgoing"] is False


def test_adapter_normalize_normal_message_not_service_notice():
    adapter = BalePvAdapter("test", {"bale_pv_phone_number": "989136421196"})
    raw = {
        "update_id": 127,
        "message": {
            "message_id": "1000",
            "date": 1,
            "chat": {"id": "456", "type": "private"},
            "from": {"id": 456, "first_name": "Sara"},
            "text": "hello",
        },
    }
    event = adapter.normalize_incoming_update(raw)
    assert event is not None
    assert event["service_notice"] is False


def _make_bridge_instance(db_session, instance_key: str) -> Instance:
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()
    instance = Instance(
        instance_key=instance_key,
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token", "inbox_id": 5}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()
    return instance


@pytest.mark.anyio
async def test_ingest_service_notice_creates_contact_only(db_session):
    """A joined-Bale service notice must create the contact but no conversation."""
    instance = _make_bridge_instance(db_session, "bale-pv-notice")

    client = AsyncMock()
    client.search_contacts = AsyncMock(return_value={"payload": []})
    client.create_contact = AsyncMock(return_value={"id": 77})
    client.create_conversation = AsyncMock(return_value={"id": 116})
    client.post_message = AsyncMock(return_value={"id": 12345})

    event = {
        "chat_id": "456",
        "chat_type": "private",
        "from_name": "Sara",
        "text": "",
        "message_id": "999",
        "platform_message_id": "999",
        "outgoing": False,
        "service_notice": True,
    }

    with patch.object(
        chatwoot_bridge,
        "_chatwoot_client_for_instance",
        return_value=(instance, {"account_id": 1, "inbox_id": 5}, client),
    ):
        result = await chatwoot_bridge.ingest_platform_event(db_session, "bale-pv-notice", event)

    assert result["ok"] is True
    assert result["contact_only"] is True
    assert result["chatwoot_contact_id"] == 77
    client.create_contact.assert_awaited_once()
    client.create_conversation.assert_not_awaited()
    client.post_message.assert_not_awaited()

    # No local conversation mapping may be created either.
    conv = (
        db_session.query(Conversation)
        .filter_by(instance_id=instance.id, platform_conversation_id="456")
        .first()
    )
    assert conv is None


@pytest.mark.anyio
async def test_ingest_service_notice_repeated_does_not_duplicate(db_session):
    """Retransmitted notices for a known contact stay conversation-free."""
    instance = _make_bridge_instance(db_session, "bale-pv-notice2")

    client = AsyncMock()
    client.search_contacts = AsyncMock(return_value={"payload": [{"id": 77}]})

    event = {
        "chat_id": "456",
        "chat_type": "private",
        "from_name": "Sara",
        "text": "",
        "message_id": "1001",
        "platform_message_id": "1001",
        "outgoing": False,
        "service_notice": True,
    }

    with patch.object(
        chatwoot_bridge,
        "_chatwoot_client_for_instance",
        return_value=(instance, {"account_id": 1, "inbox_id": 5}, client),
    ):
        result = await chatwoot_bridge.ingest_platform_event(db_session, "bale-pv-notice2", event)

    assert result["ok"] is True
    assert result["contact_only"] is True
    assert result["chatwoot_contact_id"] == 77
    client.create_contact.assert_not_awaited()
    client.create_conversation.assert_not_awaited()
    client.post_message.assert_not_awaited()


@pytest.mark.anyio
async def test_ingest_outgoing_echo_unaffected_by_flag(db_session):
    """Outgoing echoes keep the existing mirror behavior even if flagged."""
    instance = _make_bridge_instance(db_session, "bale-pv-notice3")

    client = AsyncMock()
    client.search_contacts = AsyncMock(return_value={"payload": [{"id": 77}]})
    client.list_contact_conversations = AsyncMock(return_value=[])
    client.create_conversation = AsyncMock(return_value={"id": 116})
    client.post_message = AsyncMock(return_value={"id": 12345})

    event = {
        "chat_id": "456",
        "chat_type": "private",
        "from_name": "Bale User 456",
        "text": "",
        "message_id": "1002",
        "platform_message_id": "1002",
        "outgoing": True,
        "service_notice": True,
    }

    with patch.object(
        chatwoot_bridge,
        "_chatwoot_client_for_instance",
        return_value=(instance, {"account_id": 1, "inbox_id": 5}, client),
    ):
        result = await chatwoot_bridge.ingest_platform_event(db_session, "bale-pv-notice3", event)

    assert result["ok"] is True
    assert result.get("contact_only") is not True
    assert result["chatwoot_conversation_id"] == 116
    assert result["chatwoot_message_id"] == 12345
    client.create_conversation.assert_awaited_once()
    client.post_message.assert_awaited_once()


def _chatwoot_404(method: str, url: str) -> httpx.HTTPStatusError:
    request = httpx.Request(method, url)
    response = httpx.Response(404, text="Resource could not be found", request=request)
    return httpx.HTTPStatusError("404", request=request, response=response)


@pytest.mark.anyio
async def test_ingest_recovers_when_remote_contact_deleted(db_session):
    """Contact deleted in Chatwoot: the recreate path must deactivate the
    stale local mapping (NOT null the NOT NULL chatwoot_conversation_id),
    recreate the contact + conversation remotely, and still deliver the
    message. Regression: the old code committed NULL chatwoot_conversation_id
    and died with IntegrityError, dropping every inbound message."""
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="bale-pv-contact-deleted",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token", "inbox_id": 5}',
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()

    # Stale local mapping: contact 53 + conversation 70 both deleted remotely.
    conversation = Conversation(
        instance_id=instance.id,
        platform_conversation_id="1755271951",
        chatwoot_conversation_id="70",
        chatwoot_contact_id="53",
        chatwoot_inbox_id="5",
        is_active=True,
    )
    db_session.add(conversation)
    db_session.commit()

    client = AsyncMock()
    client.post_message = AsyncMock(
        side_effect=[
            _chatwoot_404("POST", "http://chatwoot/api/v1/accounts/1/conversations/70/messages"),
            {"id": 55555},
        ]
    )
    # Contact gone: listing its conversations 404s, search finds nothing,
    # first create_conversation (with stale contact_id) 404s, then the
    # recreated contact + conversation succeed.
    client.list_contact_conversations = AsyncMock(
        side_effect=_chatwoot_404("GET", "http://chatwoot/api/v1/accounts/1/contacts/53/conversations")
    )
    client.search_contacts = AsyncMock(return_value={"payload": []})
    client.create_contact = AsyncMock(return_value={"id": 99})
    client.create_conversation = AsyncMock(
        side_effect=[
            _chatwoot_404("POST", "http://chatwoot/api/v1/accounts/1/conversations"),
            {"id": 71},
        ]
    )

    event = {
        "chat_id": "1755271951",
        "chat_type": "private",
        "from_name": "pastmaster",
        "text": "سلام",
        "message_id": "777",
        "platform_message_id": "777",
        "outgoing": False,
    }

    with patch.object(
        chatwoot_bridge,
        "_chatwoot_client_for_instance",
        return_value=(instance, {"account_id": 1, "inbox_id": 5}, client),
    ):
        result = await chatwoot_bridge.ingest_platform_event(
            db_session, "bale-pv-contact-deleted", event
        )

    assert result["ok"] is True
    assert result["chatwoot_conversation_id"] == 71
    assert result["chatwoot_message_id"] == 55555
    client.create_contact.assert_awaited_once()
    assert client.create_conversation.await_count == 2
    assert client.post_message.await_count == 2
    # The same local row is re-pointed at the fresh remote objects.
    db_session.refresh(conversation)
    assert conversation.is_active is True
    assert conversation.chatwoot_conversation_id == "71"
    assert conversation.chatwoot_contact_id == "99"
