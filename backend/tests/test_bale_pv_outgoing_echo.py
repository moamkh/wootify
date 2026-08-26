"""Tests for own-session outgoing echo mirroring and SendMessage ack capture.

Covers:
- messaging_client: send_message/send_document await the SendMessage ack
  (send_request) and degrade gracefully on ack timeout.
- dialog_parser.parse_send_message_response: tolerant rid/date extraction.
- BalePvConnector: send_text/send_media return the rid and enqueue a
  synthetic outgoing echo into runtime.message_queue when mirror_echo=True.
- ChatwootBridgeService: webhook-originated sends persist a MessageMapping
  linking the Chatwoot message id to the platform rid, which makes a later
  echo of the same message a duplicate no-op.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from bale_pv_connector.dialog_parser import parse_send_message_response
from bale_pv_connector.messaging_client import BaleMessagingClient
from bale_pv_connector.protobuf_wire import ProtobufMessage

from wootify.adapters.bale_pv import BalePvAdapter
from wootify.connectors.bale_pv_connector import (
    bale_pv as bale_pv_singleton,
    BalePvConnector,
    BalePvInstanceRuntime,
)
from wootify.models import (
    Base,
    Conversation,
    Instance,
    MessageDirection,
    MessageKind,
    MessageMapping,
    MessageStatus,
    PlatformType,
)
from wootify.services.chatwoot_bridge_service import chatwoot_bridge


@pytest.fixture()
def db():
    engine = create_engine(
        'sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


def _make_runtime(instance_key: str = "echo-test") -> BalePvInstanceRuntime:
    runtime = BalePvInstanceRuntime(
        instance_key=instance_key,
        phone_number="989130000000",
    )
    runtime.auth_state = "authenticated"
    runtime.self_user_id = 509962815
    runtime.user_cache[12345] = "Mostafa"
    return runtime


def _container_ack(rid: int, date: int, sender_uid: int = 509962815) -> bytes:
    """Build a MessageContainer-shaped SendMessage ack."""
    msg = ProtobufMessage()
    msg.add_int32(1, sender_uid)
    msg.add_int64(2, rid)
    msg.add_int64(3, date)
    inner = ProtobufMessage()
    inner.add_string(1, "hello")  # field 4 message body marker
    msg.add_bytes(4, inner.serialize())
    return msg.serialize()


# ---------------------------------------------------------------------------
# parse_send_message_response
# ---------------------------------------------------------------------------


def test_parse_send_message_response_container_layout():
    rid = 2**62 + 123
    date = 1756000000
    parsed = parse_send_message_response(_container_ack(rid, date))
    assert parsed["rid"] == rid
    assert parsed["date"] == date


def test_parse_send_message_response_compact_layout():
    rid = 2**62 + 99
    date = 1756000000
    msg = ProtobufMessage()
    msg.add_int64(1, rid)
    msg.add_int64(2, date)
    parsed = parse_send_message_response(msg.serialize())
    assert parsed["rid"] == rid
    assert parsed["date"] == date


def test_parse_send_message_response_garbage_is_safe():
    assert parse_send_message_response(None) == {"rid": None, "date": None}
    assert parse_send_message_response(b"") == {"rid": None, "date": None}
    assert parse_send_message_response(b"\xff\xff\xff") == {"rid": None, "date": None}


# ---------------------------------------------------------------------------
# messaging_client: ack capture
# ---------------------------------------------------------------------------


class _FakeWs:
    def __init__(self, response: bytes = b"ack", error: Exception | None = None):
        self.response = response
        self.error = error
        self.requests = []
        self.updates = []

    async def send_request(self, service_name, method, payload, timeout=30.0):
        self.requests.append({"service": service_name, "method": method, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.response

    async def send_update(self, service_name, method, payload):
        self.updates.append({"service": service_name, "method": method})


@pytest.mark.anyio
async def test_send_message_awaits_ack():
    client = BaleMessagingClient(jwt_token="t")
    client.ws = _FakeWs(response=b"\x08\x01")
    result = await client.send_message(peer_id=12345, text="hi")
    assert result == b"\x08\x01"
    assert client.ws.requests[0]["method"] == "SendMessage"
    # Must NOT be fire-and-forget anymore.
    assert client.ws.updates == []


@pytest.mark.anyio
async def test_send_message_ack_timeout_returns_none():
    client = BaleMessagingClient(jwt_token="t")
    client.ws = _FakeWs(error=TimeoutError("timeout"))
    result = await client.send_message(peer_id=12345, text="hi")
    assert result is None


@pytest.mark.anyio
async def test_send_document_awaits_ack():
    client = BaleMessagingClient(jwt_token="t")
    client.ws = _FakeWs(response=b"\x08\x02")
    result = await client.send_document(
        peer_id=12345,
        file_id=777,
        file_access_hash=888,
        file_size=10,
        name="voice.ogg",
        mime_type="audio/ogg",
    )
    assert result == b"\x08\x02"
    assert client.ws.requests[0]["method"] == "SendMessage"


# ---------------------------------------------------------------------------
# connector: rid propagation + synthetic echo
# ---------------------------------------------------------------------------


class _FakeMessagingClient:
    def __init__(self, ack: bytes | None):
        self.ack = ack
        self.sent = []

    async def send_message(self, peer_id, text, reply_to_message_id=None, access_hash=None):
        self.sent.append({"peer_id": peer_id, "text": text})
        return self.ack


@pytest.mark.anyio
async def test_connector_send_text_returns_rid_and_enqueues_echo():
    connector = BalePvConnector()
    runtime = _make_runtime("echo-text")
    runtime.client = _FakeMessagingClient(ack=_container_ack(2**62 + 7, 1756000000))
    connector._instances["echo-text"] = runtime

    result = await connector.send_text("echo-text", "12345", "salam")
    assert result["ok"] is True
    assert result["result"]["rid"] == 2**62 + 7
    assert result["result"]["date"] == 1756000000

    update = runtime.message_queue.get_nowait()
    message = update["message"]
    assert update["update_id"] == 2**62 + 7
    assert message["_outgoing"] is True
    assert message["text"] == "salam"
    assert message["chat"]["id"] == "12345"
    assert message["chat"]["title"] == "Mostafa"
    assert message["from"]["id"] == 509962815


@pytest.mark.anyio
async def test_connector_send_text_mirror_disabled():
    connector = BalePvConnector()
    runtime = _make_runtime("echo-off")
    runtime.client = _FakeMessagingClient(ack=_container_ack(2**62 + 8, 1756000000))
    connector._instances["echo-off"] = runtime

    result = await connector.send_text("echo-off", "12345", "salam", mirror_echo=False)
    assert result["result"]["rid"] == 2**62 + 8
    assert runtime.message_queue.empty()


@pytest.mark.anyio
async def test_connector_send_text_ack_timeout_still_mirrors_with_fallback_rid():
    connector = BalePvConnector()
    runtime = _make_runtime("echo-fallback")
    runtime.client = _FakeMessagingClient(ack=None)  # server never answered
    connector._instances["echo-fallback"] = runtime

    result = await connector.send_text("echo-fallback", "12345", "salam")
    rid = result["result"]["rid"]
    assert isinstance(rid, int) and rid > 0  # random fallback
    update = runtime.message_queue.get_nowait()
    assert update["update_id"] == rid
    assert update["message"]["_outgoing"] is True


def test_enqueue_outgoing_echo_voice_media_shape():
    """A synthesized voice echo must look exactly like a server-pushed one."""
    connector = BalePvConnector()
    runtime = _make_runtime("echo-media")
    connector._instances["echo-media"] = runtime

    rid = connector._enqueue_outgoing_echo(
        runtime,
        chat_id="12345",
        rid=2**62 + 42,
        date=1756000000,
        text="",
        media={
            "file_id": 8521755718725803778,
            "access_hash": 12345,
            "file_name": "voice.ogg",
            "mime_type": "audio/ogg; codecs=opus",
            "file_storage_version": 1,
        },
    )
    assert rid == 2**62 + 42
    update = runtime.message_queue.get_nowait()
    message = update["message"]
    assert message["_outgoing"] is True
    assert "voice" in message
    composite = json.loads(message["voice"]["file_id"])
    assert composite["file_id"] == 8521755718725803778
    assert composite["access_hash"] == 12345
    assert composite["peer_id"] == 12345
    assert composite["file_storage_version"] == 1


def test_synthesized_echo_flows_through_parse_and_adapter():
    """The synthesized dict must pass _parse_raw_update unchanged and
    normalize into an outgoing event with an attachment ref."""
    connector = BalePvConnector()
    runtime = _make_runtime("echo-flow")
    connector._instances["echo-flow"] = runtime
    # The adapter consults the singleton for self-id and peer display names.
    bale_pv_singleton._instances["echo-flow"] = runtime
    try:
        connector._enqueue_outgoing_echo(
            runtime,
            chat_id="12345",
            rid=2**62 + 43,
            date=1756000000,
            text="",
            media={
                "file_id": 111,
                "access_hash": 222,
                "file_name": "voice.ogg",
                "mime_type": "audio/ogg",
                "file_storage_version": 1,
            },
        )
        raw = runtime.message_queue.get_nowait()

        parsed = BalePvConnector._parse_raw_update(
            raw, runtime.user_cache, runtime.self_user_id, runtime.chat_title_cache
        )
        assert parsed is raw  # dicts pass through unchanged

        adapter = BalePvAdapter("echo-flow", {"bale_pv_phone_number": "989130000000"})
        event = adapter.normalize_incoming_update(parsed)
        assert event is not None
        assert event["outgoing"] is True
        assert event["chat_id"] == "12345"
        assert event["from_name"] == "Mostafa"
        assert event["message_id"] == str(2**62 + 43)
        assert len(event["attachments"]) == 1
        assert event["attachments"][0]["filename"] == "voice.ogg"
    finally:
        bale_pv_singleton._instances.pop("echo-flow", None)


# ---------------------------------------------------------------------------
# bridge: outbound mapping persistence + echo dedup
# ---------------------------------------------------------------------------


def _make_bridge_instance(db, instance_key: str) -> Instance:
    platform = PlatformType(
        key="bale_pv_enterprise",
        display_name="Bale PV Enterprise",
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
        chatwoot_config_encrypted='{"account_id": 1, "base_url": "http://chatwoot", "api_access_token": "token", "inbox_id": 5}',
        proxy_config_encrypted="",
    )
    db.add(instance)
    db.commit()
    return instance


def _make_conversation(db, instance, peer_id="12345", cw_conv="77", cw_contact="42") -> Conversation:
    conv = Conversation(
        instance_id=instance.id,
        platform_conversation_id=peer_id,
        chatwoot_conversation_id=cw_conv,
        chatwoot_contact_id=cw_contact,
        is_active=True,
    )
    db.add(conv)
    db.commit()
    return conv


@pytest.mark.anyio
async def test_webhook_send_persists_outbound_mapping(db):
    """A webhook-originated send must link the Chatwoot message id to the
    platform rid returned by the send ack."""
    instance = _make_bridge_instance(db, "bale-pv-outmap")

    rid = 2**62 + 100
    adapter = MagicMock()
    adapter.send_text = AsyncMock(
        return_value={"ok": True, "result": {"ok": True, "result": {"rid": rid, "date": 1}}}
    )
    runtime = MagicMock()
    runtime.platform_type = "bale_pv_enterprise"
    runtime.status = "open"
    runtime.adapter = adapter

    client = AsyncMock()

    payload = {
        "event": "message_created",
        "message_type": "outgoing",
        "id": 900,
        "content": "Hello",
        "conversation": {
            "id": 77,
            "meta": {"sender": {"id": 42, "identifier": "BALE_PV:12345"}},
            "messages": [],
        },
    }

    with patch("wootify.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
        with patch.object(
            chatwoot_bridge,
            "_chatwoot_client_for_instance",
            return_value=(instance, {"account_id": 1}, client),
        ):
            result = await chatwoot_bridge.handle_chatwoot_webhook(db, "bale-pv-outmap", payload)

    assert result["ok"] is True
    adapter.send_text.assert_awaited_once_with("12345", "Hello", reply_to=None, mirror_echo=False)

    mapping = (
        db.query(MessageMapping)
        .filter(MessageMapping.chatwoot_message_id == "900")
        .first()
    )
    assert mapping is not None
    assert mapping.platform_message_id == str(rid)
    assert mapping.direction == MessageDirection.chatwoot_to_platform
    assert mapping.status == MessageStatus.sent
    assert (mapping.platform_payload_json or {}).get("text") == "Hello"


@pytest.mark.anyio
async def test_outbound_mapping_makes_echo_dedup(db):
    """A (synthesized or server-pushed) outgoing echo for a webhook-sent
    message must be a no-op via duplicate_platform_message_skip."""
    instance = _make_bridge_instance(db, "bale-pv-echodedup")
    conv = _make_conversation(db, instance)
    rid = 2**62 + 101

    # Simulate what handle_chatwoot_webhook persists after a successful send.
    chatwoot_bridge._persist_outbound_mappings(
        db,
        instance=instance,
        peer_id="12345",
        chatwoot_message_id=900,
        sent=[{"ok": True, "result": {"ok": True, "result": {"rid": rid}}}],
        text="hello",
        message_kind=MessageKind.text,
    )

    client = AsyncMock()
    client.post_message = AsyncMock(return_value={"id": 99999})

    echo_event = {
        "chat_id": "12345",
        "chat_type": "private",
        "from_name": "Mostafa",
        "text": "hello",
        "message_id": str(rid),
        "platform_message_id": str(rid),
        "outgoing": True,
    }
    with patch.object(
        chatwoot_bridge,
        "_chatwoot_client_for_instance",
        return_value=(instance, {"account_id": 1, "inbox_id": 5}, client),
    ):
        result = await chatwoot_bridge.ingest_platform_event(db, "bale-pv-echodedup", echo_event)

    assert result["ok"] is True
    assert result.get("duplicate") is True
    assert result["chatwoot_message_id"] == "900"
    # No second message may be posted to Chatwoot.
    client.post_message.assert_not_awaited()

    # The mapping still points at the original conversation.
    mapping = (
        db.query(MessageMapping)
        .filter(
            MessageMapping.conversation_id == str(conv.id),
            MessageMapping.platform_message_id == str(rid),
        )
        .first()
    )
    assert mapping is not None
    assert mapping.chatwoot_message_id == "900"
