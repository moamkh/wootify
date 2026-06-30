"""Tests for bale_pv_connector.update_parser.

These tests use synthetic protobuf frames so they do not depend on captured
WebSocket logs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_bale_pv_connector_path = str(Path(__file__).resolve().parent.parent / "bale_pv_connector" / "src")
if _bale_pv_connector_path not in sys.path:
    sys.path.insert(0, _bale_pv_connector_path)
from bale_pv_connector.protobuf_wire import ProtobufMessage
from bale_pv_connector.update_parser import BaleUpdateType, parse_ws_update


def _build_update_frame(wrapper_field: int, wrapper_payload: bytes) -> bytes:
    """Build a minimal WebSocket update frame.

    Frame layout:
        Outer {1: Inner}
        Inner {1: Wrapper}
        Wrapper {wrapper_field: payload}
    """
    wrapper = ProtobufMessage()
    wrapper.add_bytes(wrapper_field, wrapper_payload)

    inner = ProtobufMessage()
    inner.add_message(1, wrapper)

    outer = ProtobufMessage()
    outer.add_message(1, inner)
    return outer.serialize()


def _build_inner_status_frame(peer_type: int, peer_id: int) -> bytes:
    """Build a frame where the inner wrapper carries fields 4/5 status data."""
    peer = ProtobufMessage()
    peer.add_int32(1, peer_type)
    peer.add_int64(2, peer_id)

    status = ProtobufMessage()
    status.add_message(1, peer)
    status.add_int64(2, 5555)

    inner = ProtobufMessage()
    inner.add_int64(4, 1234567890)
    inner.add_bytes(5, status.serialize())

    outer = ProtobufMessage()
    outer.add_message(1, inner)
    return outer.serialize()


def _build_text_message(text: str) -> bytes:
    """Build a Message(G) containing a textMessage (field 15)."""
    text_msg = ProtobufMessage()
    text_msg.add_string(1, text)
    msg = ProtobufMessage()
    msg.add_bytes(15, text_msg.serialize())
    return msg.serialize()


def _build_document_message(
    file_id: int,
    access_hash: int,
    filename: str,
    mime_type: str,
    caption: str = "",
) -> bytes:
    """Build a Message(G) containing a documentMessage (field 4)."""
    doc = ProtobufMessage()
    doc.add_int64(1, file_id)
    doc.add_int64(2, access_hash)
    doc.add_string(4, filename)
    doc.add_string(5, mime_type)
    if caption:
        caption_msg = ProtobufMessage()
        caption_msg.add_string(1, caption)
        doc.add_bytes(8, caption_msg.serialize())
    msg = ProtobufMessage()
    msg.add_bytes(4, doc.serialize())
    return msg.serialize()


def _build_peer(peer_type: int, peer_id: int) -> bytes:
    peer = ProtobufMessage()
    peer.add_int32(1, peer_type)
    peer.add_int64(2, peer_id)
    return peer.serialize()


def _build_forward_header(
    from_peer_type: int,
    from_peer_id: int,
    original_message_id: int,
    message_bytes: bytes,
) -> bytes:
    """Build UpdateMessage field 7 forward header."""
    header = ProtobufMessage()
    header.add_bytes(1, _build_peer(from_peer_type, from_peer_id))
    header.add_bytes(2, _build_peer(from_peer_type, from_peer_id))
    header.add_int32(3, 12345)
    header.add_int64(4, original_message_id)
    header.add_bytes(5, message_bytes)
    return header.serialize()


def _build_update_message(
    peer_type: int,
    peer_id: int,
    sender_uid: int,
    text: str = "",
    rid: int = 9999,
    date: int = 1234567890,
    forward_header: bytes = b"",
    reply_to_msg_id: int = 0,
) -> bytes:
    """Build an UpdateMessage (field 55) payload."""
    peer = _build_peer(peer_type, peer_id)

    update = ProtobufMessage()
    update.add_bytes(1, peer)
    update.add_int32(2, sender_uid)
    update.add_int64(3, date)
    update.add_int64(4, rid)
    if text:
        update.add_bytes(5, _build_text_message(text))
    if forward_header:
        update.add_bytes(7, forward_header)
    if reply_to_msg_id:
        reply = ProtobufMessage()
        reply.add_int64(4, reply_to_msg_id)
        update.add_bytes(6, reply.serialize())
    return update.serialize()


def _build_status_heartbeat_frame(peer_type: int, peer_id: int) -> bytes:
    """Build a status/heartbeat wrapper frame (fields 4/5)."""
    peer = ProtobufMessage()
    peer.add_int32(1, peer_type)
    peer.add_int64(2, peer_id)

    status = ProtobufMessage()
    status.add_message(1, peer)
    status.add_int64(2, 5555)

    wrapper = ProtobufMessage()
    wrapper.add_int64(4, 1234567890)
    wrapper.add_bytes(5, status.serialize())

    inner = ProtobufMessage()
    inner.add_message(1, wrapper)

    outer = ProtobufMessage()
    outer.add_message(1, inner)
    return outer.serialize()


def _build_contact_status_frame(peer_type: int, peer_id: int) -> bytes:
    """Build a contactStatus wrapper frame (field 46)."""
    peer = ProtobufMessage()
    peer.add_int32(1, peer_type)
    peer.add_int64(2, peer_id)

    contact = ProtobufMessage()
    contact.add_message(1, peer)
    contact.add_int64(2, 12345)

    wrapper = ProtobufMessage()
    wrapper.add_bytes(46, contact.serialize())

    inner = ProtobufMessage()
    inner.add_message(1, wrapper)

    outer = ProtobufMessage()
    outer.add_message(1, inner)
    return outer.serialize()


def _build_read_receipt_frame(peer_type: int, peer_id: int) -> bytes:
    """Build a readReceipt wrapper frame (field 50)."""
    peer = ProtobufMessage()
    peer.add_int32(1, peer_type)
    peer.add_int64(2, peer_id)

    receipt = ProtobufMessage()
    receipt.add_message(1, peer)
    receipt.add_int64(2, 1111)
    receipt.add_int64(2, 2222)

    wrapper = ProtobufMessage()
    wrapper.add_bytes(50, receipt.serialize())

    inner = ProtobufMessage()
    inner.add_message(1, wrapper)

    outer = ProtobufMessage()
    outer.add_message(1, inner)
    return outer.serialize()


def test_parse_text_message_frame() -> None:
    frame = _build_update_frame(
        BaleUpdateType.NEW_MESSAGE,
        _build_update_message(peer_type=1, peer_id=12345, sender_uid=42, text="hello"),
    )
    parsed = parse_ws_update(frame)
    assert parsed is not None
    assert parsed["type"] == "message"
    assert parsed["sender_uid"] == 42
    assert parsed["peer"] == {"type": 1, "id": 12345}
    assert parsed["text"] == "hello"


def test_parse_group_message_frame() -> None:
    frame = _build_update_frame(
        BaleUpdateType.NEW_MESSAGE,
        _build_update_message(peer_type=2, peer_id=100500, sender_uid=42, text="hello"),
    )
    parsed = parse_ws_update(frame)
    assert parsed is not None
    assert parsed["type"] == "message"
    assert parsed["sender_uid"] == 42
    assert parsed["peer"] == {"type": 2, "id": 100500}
    assert parsed["text"] == "hello"
    assert parsed["date"] == 1234567890


def test_parse_forwarded_document_frame() -> None:
    """Forwarded media with caption is read from the forward header (field 7)."""
    forwarded_doc = _build_document_message(
        file_id=111,
        access_hash=222,
        filename="report.pdf",
        mime_type="application/pdf",
        caption="see attached",
    )
    forward_header = _build_forward_header(
        from_peer_type=1,
        from_peer_id=777,
        original_message_id=555,
        message_bytes=forwarded_doc,
    )
    update = _build_update_message(
        peer_type=1,
        peer_id=12345,
        sender_uid=42,
        forward_header=forward_header,
    )
    frame = _build_update_frame(BaleUpdateType.NEW_MESSAGE, update)
    parsed = parse_ws_update(frame)
    assert parsed is not None
    assert parsed["type"] == "message"
    assert parsed["text"] == "see attached"
    assert parsed.get("message_type") == "document"
    media = parsed.get("media")
    assert media is not None
    assert media["file_name"] == "report.pdf"
    assert media["mime_type"] == "application/pdf"
    assert parsed.get("forward_from") == {
        "from_id": 777,
        "forward_message_id": 555,
        "forward_date": 12345,
    }
    assert "reply_to_msg_id" not in parsed


def test_parse_status_heartbeat_frame() -> None:
    frame = _build_status_heartbeat_frame(peer_type=2, peer_id=100500)
    parsed = parse_ws_update(frame)
    assert parsed is not None
    assert parsed["type"] == "status"
    assert parsed["peer"] == {"type": 2, "id": 100500}
    assert parsed["reference_id"] == "5555"


def test_parse_contact_status_frame() -> None:
    frame = _build_contact_status_frame(peer_type=2, peer_id=100500)
    parsed = parse_ws_update(frame)
    assert parsed is not None
    assert parsed["type"] == "contact_status"
    assert parsed["peer"] == {"type": 2, "id": 100500}


def test_parse_read_receipt_frame() -> None:
    frame = _build_read_receipt_frame(peer_type=1, peer_id=12345)
    parsed = parse_ws_update(frame)
    assert parsed is not None
    assert parsed["type"] == "read_receipt"
    assert parsed["peer"] == {"type": 1, "id": 12345}
    assert parsed["reference_ids"] == ["1111", "2222"]


def test_parse_known_unhandled_frame() -> None:
    """Call-signaling and other known-but-not-parsed fields are recognized."""
    wrapper = ProtobufMessage()
    wrapper.add_bytes(52807, b"\x08\x01")

    container = ProtobufMessage()
    container.add_bytes(1, wrapper.serialize())

    inner = ProtobufMessage()
    inner.add_message(1, container)

    outer = ProtobufMessage()
    outer.add_message(2, inner)

    parsed = parse_ws_update(outer.serialize())
    assert parsed is not None
    assert parsed["type"] == "known_unhandled"
    assert 52807 in parsed["unhandled_fields"]


def test_parse_update_message_field_13_reply_to():
    """Field 13 in UpdateMessage is parsed as a reply-to reference."""
    peer = ProtobufMessage()
    peer.add_int32(1, 1)
    peer.add_int64(2, 1707272132)

    text_msg = ProtobufMessage()
    text_msg.add_string(1, "hi")
    msg = ProtobufMessage()
    msg.add_bytes(15, text_msg.serialize())

    reply_ref = ProtobufMessage()
    reply_ref.add_int64(1, 9876543210)
    reply_ref.add_int64(2, 12345678901234567890)

    update = ProtobufMessage()
    update.add_message(1, peer)
    update.add_int32(2, 1707272132)
    update.add_int64(3, 65097)
    update.add_int64(4, 14913833318558763934)
    update.add_message(5, msg)
    update.add_message(13, reply_ref)

    frame = _build_update_frame(BaleUpdateType.NEW_MESSAGE, update.serialize())
    result = parse_ws_update(frame)
    assert result is not None
    assert result["type"] == "message"
    assert result["reply_to_msg_id"] == 9876543210
