"""Unit tests for the Bale PV adapter and Chatwoot bridge helpers."""

from __future__ import annotations

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.connectors.bale_pv_connector import bale_pv as bale_pv_connector, BalePvConnector

from app.adapters.bale_pv import BalePvAdapter
from app.models import BalePvPhoneResolvedUser, Conversation, Instance, PlatformType
from app.services.chatwoot_bridge_service import ChatwootBridgeService, chatwoot_bridge


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
        "app.adapters.bale_pv.bale_pv.download_file_by_id",
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

    with patch("app.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
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
    adapter.send_text.assert_awaited_once_with("12345", "Hello", reply_to=None)


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

    with patch("app.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
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

    with patch("app.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
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
    adapter.send_text.assert_awaited_once_with("770408072", "Hello", reply_to=None)
    client.update_contact.assert_not_awaited()


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

    with patch("app.services.chatwoot_bridge_service.get_runtime", return_value=runtime):
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
        "app.adapters.bale_pv.bale_pv.download_file_by_id",
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
        "app.adapters.bale_pv.bale_pv.download_file_by_id",
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
    from bale_grpc_client.messaging_messages import SendTypeValue

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
    from bale_grpc_client.messaging_messages import FastThumb, ImageExt

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
    from bale_grpc_client.messaging_messages import AudioExt

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
    from bale_grpc_client.messaging_messages import (
        DocumentMessage, FastThumb, ImageExt, TextMessage
    )
    from bale_grpc_client.protobuf_wire import ProtobufParser

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

    from app.models import MessageDirection, MessageKind, MessageStatus

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
    from app.models import MessageMapping
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

    from app.models import MessageDirection, MessageKind, MessageMapping, MessageStatus

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

    from app.models import MessageDirection, MessageKind, MessageMapping, MessageStatus

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
async def test_post_message_with_attachments_does_not_retry_on_timeout():
    """Media uploads that time out must not be retried to avoid duplicates."""
    from app.clients.chatwoot_client import ChatwootClient

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
    from app.clients.chatwoot_client import ChatwootClient

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
async def test_get_request_still_retries_on_timeout():
    """GET requests should still retry on ReadTimeout."""
    from app.clients.chatwoot_client import ChatwootClient

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
