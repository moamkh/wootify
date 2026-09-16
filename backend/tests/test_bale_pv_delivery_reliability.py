"""Regression coverage for routed delivery, failed status and Bale seen RPCs."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import wootify.services.chatwoot_bridge_service as bridge_module
import wootify.presentation.http.routers._handler_controller as webhook_controller
from wootify.application.messaging.chatwoot_bridge import ChatwootBridgeService
from wootify.application.messaging.destination_resolver import DestinationResolver
from wootify.infrastructure.persistence.models import (
    Conversation,
    ChatwootWebhookDelivery,
    Base,
    Instance,
    MessageDirection,
    MessageKind,
    MessageMapping,
    MessageStatus,
    PlatformType,
)
from wootify.plugins.bale_pv.connector import BalePvConnector, BalePvInstanceRuntime
from wootify.plugins.bale_pv.adapter import BalePvAdapter
from bale_pv_connector.messaging_messages import Peer, SendMessageRequest
from bale_pv_connector.protobuf_wire import ProtobufMessage, ProtobufParser
from bale_pv_connector.update_parser import BaleUpdateType


KNOWN_PREFIXES = {
    "BALE",
    "BALE_ENTERPRISE",
    "BALE_PV",
    "EITAA_PV",
    "INSTAGRAM_PV",
    "TELEGRAM",
    "TELEGRAM_ENTERPRISE",
}


@pytest.mark.parametrize(
    ("raw", "expected_prefix", "peer_id", "peer_type"),
    [
        ("BALE_ENTERPRISE:novin-bale-enterprise:703074518", "BALE_PV", "703074518", None),
        ("TELEGRAM_ENTERPRISE:support-telegram:-100123", "TELEGRAM", "-100123", None),
        ("EITAA_PV:-19040796", "EITAA_PV", "-19040796", None),
        ("INSTAGRAM_PV:340282366841", "INSTAGRAM_PV", "340282366841", None),
        ("BALE_PV:GROUP:205505657", "BALE_PV", "205505657", "group"),
        ("BALE_PV:CHANNEL:1865636070", "BALE", "1865636070", "channel"),
    ],
)
def test_all_registered_identifier_shapes_are_parsed(
    raw, expected_prefix, peer_id, peer_type
):
    actual_id, actual_type, source_prefix = DestinationResolver.compatible_platform_destination(
        raw,
        expected_prefix=expected_prefix,
        known_prefixes=KNOWN_PREFIXES,
    )
    assert actual_id == peer_id
    assert actual_type == peer_type
    assert source_prefix


def test_foreign_platform_identifier_is_not_cross_routed():
    peer_id, peer_type, source_prefix = DestinationResolver.compatible_platform_destination(
        "EITAA_PV:123",
        expected_prefix="TELEGRAM",
        known_prefixes=KNOWN_PREFIXES,
    )
    assert (peer_id, peer_type, source_prefix) == (None, None, "EITAA_PV")


def test_group_send_request_serializes_group_peer_type():
    request = SendMessageRequest(peer_id=205505657, peer_type=2, text="sample")
    outer = ProtobufParser(request.serialize()).parse()
    peer = ProtobufParser(outer[1][0]).parse()
    assert peer[1][0] == 2
    assert peer[2][0] == 205505657


def test_deleted_wire_update_reaches_adapter_as_deleted_event():
    content = ProtobufMessage().add_message(
        3, ProtobufMessage(), include_empty=True
    )
    update = ProtobufMessage()
    update.add_bytes(1, Peer(123).serialize())
    update.add_int32(2, 123)
    update.add_int64(4, 9020)
    update.add_bytes(5, content.serialize())
    wrapper = ProtobufMessage().add_bytes(
        BaleUpdateType.NEW_MESSAGE, update.serialize()
    )
    inner = ProtobufMessage().add_bytes(1, wrapper.serialize())
    frame = ProtobufMessage().add_bytes(1, inner.serialize()).serialize()

    normalized = BalePvConnector._parse_raw_update(
        frame,
        user_cache={123: "Sample"},
        self_user_id=999,
    )
    assert normalized is not None
    assert normalized["message"]["_deleted"] is True
    assert normalized["message"].get("_service_notice") is not True

    adapter = BalePvAdapter("delete-sample", {})
    event = adapter.normalize_incoming_update(normalized)
    assert event is not None
    assert event["deleted"] is True
    assert event["platform_message_id"] == "9020"


def test_web_bale_dedicated_delete_reaches_adapter_as_deleted_event():
    reference = ProtobufMessage()
    reference.add_int64(1, 1789547704022)
    reference.add_int64(2, 9407350905859307013)
    deleted = ProtobufMessage()
    deleted.add_bytes(1, reference.serialize())
    deleted.add_bytes(2, Peer(1755271951).serialize())
    wrapper = ProtobufMessage().add_bytes(
        BaleUpdateType.DELETE_MESSAGE, deleted.serialize()
    )
    container = ProtobufMessage()
    container.add_bytes(1, wrapper.serialize())
    container.add_int64(4, 1789547751768)
    inner = ProtobufMessage().add_bytes(1, container.serialize())
    frame = ProtobufMessage().add_bytes(2, inner.serialize()).serialize()

    normalized = BalePvConnector._parse_raw_update(
        frame,
        user_cache={},
        self_user_id=999,
    )
    assert normalized is not None
    assert normalized["message"]["chat"]["id"] == "1755271951"
    assert normalized["message"]["message_id"] == "9407350905859307013"
    assert normalized["message"]["_deleted"] is True

    adapter = BalePvAdapter("delete-sample", {})
    event = adapter.normalize_incoming_update(normalized)
    assert event is not None
    assert event["deleted"] is True
    assert event["platform_message_id"] == "9407350905859307013"
    assert event["chat_id"] == "1755271951"


def test_senderless_group_delete_uses_authoritative_peer():
    content = ProtobufMessage().add_message(
        3, ProtobufMessage(), include_empty=True
    )
    channel_update = ProtobufMessage()
    channel_update.add_bytes(
        1, Peer(777, Peer.PEER_TYPE_GROUP).serialize()
    )
    channel_update.add_int64(2, 9021)
    channel_update.add_bytes(3, content.serialize())
    channel_update.add_int64(5, 1784128179040)
    wrapper = ProtobufMessage().add_bytes(
        BaleUpdateType.CHANNEL_MESSAGE, channel_update.serialize()
    )
    inner = ProtobufMessage().add_bytes(1, wrapper.serialize())
    frame = ProtobufMessage().add_bytes(1, inner.serialize()).serialize()

    normalized = BalePvConnector._parse_raw_update(
        frame,
        user_cache={},
        self_user_id=999,
    )

    assert normalized is not None
    assert normalized["message"]["chat"] == {
        "id": "777",
        "type": "channel",
        "title": "Channel 777",
    }
    assert normalized["message"]["message_id"] == "9021"
    assert normalized["message"]["_deleted"] is True


def _instance(db_session, key: str = "routed-bale") -> Instance:
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()
    instance = Instance(
        instance_key=key,
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted="",
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()
    return instance


def _payload(identifier: str, *, message_id: int = 9001, conversation_id: int = 77):
    return {
        "event": "message_created",
        "id": message_id,
        "message_type": "outgoing",
        "content": "sample reply",
        "conversation": {
            "id": conversation_id,
            "inbox_id": 5,
            "meta": {"sender": {"id": 42, "identifier": identifier}},
            "messages": [],
        },
    }


@pytest.mark.anyio
async def test_bale_pv_routes_enterprise_composite_identifier(db_session, monkeypatch):
    instance = _instance(db_session)
    adapter = AsyncMock()
    adapter.send_text.return_value = {"ok": True, "result": {"result": {"rid": 1234}}}
    runtime = SimpleNamespace(status="open", platform_type="bale_pv_enterprise", adapter=adapter)
    client = AsyncMock()
    service = ChatwootBridgeService()
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: runtime)
    monkeypatch.setattr(
        service,
        "_chatwoot_client_for_instance",
        lambda *_: (instance, {"account_id": 1}, client),
    )

    result = await service.handle_chatwoot_webhook(
        db_session,
        instance.instance_key,
        _payload("BALE_ENTERPRISE:novin-bale-enterprise:703074518"),
    )

    assert result["ok"] is True
    assert result["peer_id"] == "703074518"
    adapter.send_text.assert_awaited_once_with(
        "703074518", "sample reply", reply_to=None, mirror_echo=False
    )


@pytest.mark.anyio
async def test_instance_conversation_mapping_wins_over_contact_identifier(db_session, monkeypatch):
    instance = _instance(db_session, "mapped-bale")
    db_session.add(
        Conversation(
            instance_id=instance.id,
            platform_conversation_id="888",
            chatwoot_conversation_id="78",
            chatwoot_contact_id="42",
            chatwoot_inbox_id="5",
            is_active=True,
        )
    )
    db_session.commit()
    adapter = AsyncMock()
    adapter.send_text.return_value = {"ok": True, "result": {"result": {"rid": 1235}}}
    runtime = SimpleNamespace(status="open", platform_type="bale_pv_enterprise", adapter=adapter)
    client = AsyncMock()
    service = ChatwootBridgeService()
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: runtime)
    monkeypatch.setattr(service, "_chatwoot_client_for_instance", lambda *_: (instance, {"account_id": 1}, client))

    result = await service.handle_chatwoot_webhook(
        db_session,
        instance.instance_key,
        _payload("BALE_ENTERPRISE:other:999", message_id=9002, conversation_id=78),
    )

    assert result["peer_id"] == "888"
    adapter.send_text.assert_awaited_once_with(
        "888", "sample reply", reply_to=None, mirror_echo=False
    )


@pytest.mark.anyio
async def test_failed_delivery_marks_original_message_and_keeps_private_note():
    service = ChatwootBridgeService()
    client = AsyncMock()
    payload = _payload("BALE_PV:USER:123", message_id=9010, conversation_id=79)

    await service._notify_delivery_failure(
        client,
        1,
        payload,
        "123",
        ConnectionError("WebSocket not connected"),
        platform_type="bale_pv_enterprise",
    )

    client.update_message_status.assert_awaited_once_with(
        1,
        79,
        9010,
        status="failed",
        external_error="Bale PV: ConnectionError: WebSocket not connected",
    )
    client.post_message.assert_awaited_once()
    assert client.post_message.await_args.args[2]["private"] is True


@pytest.mark.anyio
async def test_disconnected_instance_marks_outgoing_message_failed(db_session, monkeypatch):
    instance = _instance(db_session, "disconnected-bale")
    client = AsyncMock()
    service = ChatwootBridgeService()
    monkeypatch.setattr(bridge_module, "get_runtime", lambda _: None)
    monkeypatch.setattr(
        service,
        "_chatwoot_client_for_instance",
        lambda *_: (instance, {"account_id": 1}, client),
    )

    result = await service.handle_chatwoot_webhook(
        db_session,
        instance.instance_key,
        _payload("BALE_PV:USER:123", message_id=9011, conversation_id=80),
    )

    assert result == {"ok": False, "detail": "instance_not_connected"}
    client.update_message_status.assert_awaited_once_with(
        1,
        80,
        9011,
        status="failed",
        external_error="Bale PV: RuntimeError: instance_not_connected",
    )
    client.post_message.assert_awaited_once()


@pytest.mark.anyio
async def test_outbound_mapping_is_committed_before_platform_prepare(db_session):
    instance = _instance(db_session, "mapping-transaction-bale")
    service = ChatwootBridgeService()

    await service._persist_outbound_conversation_mapping(
        db=db_session,
        instance_id=instance.id,
        platform_conversation_id="123",
        chatwoot_conversation_id="8123",
        chatwoot_contact_id="42",
        chatwoot_inbox_id="5",
    )

    assert db_session.in_transaction() is False
    row = (
        db_session.query(Conversation)
        .filter(
            Conversation.instance_id == instance.id,
            Conversation.platform_conversation_id == "123",
        )
        .one()
    )
    assert row.chatwoot_conversation_id == "8123"


def test_chatwoot_webhook_is_durable_and_failed_retry_rearms(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'deliveries.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(webhook_controller, "SessionLocal", factory)
    payload = _payload("BALE_PV:USER:123", message_id=9911, conversation_id=8811)
    with factory() as session:
        delivery_id = webhook_controller._persist_chatwoot_delivery(
            session,
            instance_key="durable-bale",
            platform_key="bale_pv_enterprise",
            route_key=None,
            payload=payload,
        )
        duplicate_id = webhook_controller._persist_chatwoot_delivery(
            session,
            instance_key="durable-bale",
            platform_key="bale_pv_enterprise",
            route_key=None,
            payload=payload,
        )
        assert duplicate_id == delivery_id

        webhook_controller._fail_chatwoot_delivery(
            delivery_id, "PermissionDenied", terminal=True
        )
        session.expire_all()
        failed = session.get(ChatwootWebhookDelivery, delivery_id)
        assert failed is not None
        assert failed.status == "failed"

        rearmed_id = webhook_controller._persist_chatwoot_delivery(
            session,
            instance_key="durable-bale",
            platform_key="bale_pv_enterprise",
            route_key=None,
            payload=payload,
        )
        session.expire_all()
        assert rearmed_id == delivery_id
        assert session.get(ChatwootWebhookDelivery, delivery_id).status == "pending"
    engine.dispose()


@pytest.mark.anyio
async def test_bale_deleted_message_is_soft_deleted_in_chatwoot(db_session, monkeypatch):
    instance = _instance(db_session, "deleted-in-bale")
    conversation = Conversation(
        instance_id=instance.id,
        platform_conversation_id="123",
        chatwoot_conversation_id="81",
        chatwoot_contact_id="42",
        chatwoot_inbox_id="5",
        is_active=True,
    )
    db_session.add(conversation)
    db_session.flush()
    mapping = MessageMapping(
        conversation_id=conversation.id,
        direction=MessageDirection.platform_to_chatwoot,
        message_kind=MessageKind.text,
        status=MessageStatus.sent,
        platform_message_id="9020",
        chatwoot_message_id="301",
        platform_payload_json={"text": "remove me"},
    )
    db_session.add(mapping)
    db_session.commit()
    client = AsyncMock()
    service = ChatwootBridgeService()
    monkeypatch.setattr(
        service,
        "_chatwoot_client_for_instance",
        lambda *_: (instance, {"account_id": 1, "inbox_id": 5}, client),
    )
    event = {
        "chat_id": "123",
        "chat_type": "private",
        "from_name": "Sample",
        "message_id": "9020",
        "platform_message_id": "9020",
        "deleted": True,
    }

    result = await service.ingest_platform_event(
        db_session, instance.instance_key, event
    )
    duplicate = await service.ingest_platform_event(
        db_session, instance.instance_key, event
    )

    assert result["status"] == "delete_propagated"
    assert duplicate["reason"] == "already_deleted"
    client.delete_message.assert_awaited_once_with(
        account_id=1,
        conversation_id=81,
        message_id=301,
    )
    db_session.refresh(mapping)
    assert mapping.platform_payload_json == {
        "text": "remove me",
        "deleted": True,
    }

    loop_result = await service._handle_chatwoot_message_deleted(
        db_session,
        instance,
        {
            "id": 301,
            "conversation": {"id": 81},
            "content_attributes": {"deleted": True},
        },
    )
    assert loop_result["reason"] == "not_chatwoot_outbound"


@pytest.mark.anyio
async def test_sample_message_is_marked_seen_once_before_replies():
    """A controlled sample inbound message advances the seen cache once."""
    connector = BalePvConnector()
    client = AsyncMock()
    runtime = BalePvInstanceRuntime(
        instance_key="seen-sample",
        phone_number="989120000000",
        client=client,
        auth_state="authenticated",
    )
    runtime.pending_read_message_ids["123"] = 456
    connector._instances[runtime.instance_key] = runtime

    await connector.prepare_outbound("seen-sample", "123", peer_type="user")
    await connector.prepare_outbound("seen-sample", "123", peer_type="user")

    client.message_read.assert_awaited_once_with(
        123,
        456,
        peer_type=1,
        access_hash=0,
    )
    assert runtime.read_message_ids["123"] == 456
