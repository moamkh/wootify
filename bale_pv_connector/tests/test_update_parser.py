"""Tests for WebSocket update parser."""

import pytest

from bale_pv_connector.messaging_messages import Peer, TextMessage
from bale_pv_connector.protobuf_wire import ProtobufMessage
from bale_pv_connector.update_parser import BaleUpdateType, parse_ws_update


def _build_text_message(text: str) -> bytes:
    msg = ProtobufMessage()
    msg.add_message(15, TextMessage(text))  # Message G -> textMessage
    return msg.serialize()


def _build_update_message_frame(
    peer_id: int,
    sender_uid: int,
    rid: int,
    text: str,
) -> bytes:
    """Build a minimal WebSocket update frame for a new text message."""
    peer = Peer(peer_id).serialize()
    message = _build_text_message(text)

    update = ProtobufMessage()
    update.add_bytes(1, peer)
    update.add_int32(2, sender_uid)
    update.add_int64(4, rid)
    update.add_bytes(5, message)

    wrapper = ProtobufMessage()
    wrapper.add_bytes(BaleUpdateType.NEW_MESSAGE, update.serialize())

    inner = ProtobufMessage()
    inner.add_bytes(1, wrapper.serialize())

    outer = ProtobufMessage()
    outer.add_bytes(1, inner.serialize())

    return outer.serialize()


def test_parse_new_text_message_update() -> None:
    frame = _build_update_message_frame(peer_id=123, sender_uid=456, rid=789, text="hello")
    parsed = parse_ws_update(frame)
    assert parsed is not None
    assert parsed["type"] == "message"
    assert parsed["sender_uid"] == 456
    assert parsed["rid"] == "789"
    assert parsed["peer"] == {"type": 1, "id": 123}
    assert parsed["text"] == "hello"
    assert parsed["message_type"] == "text"


def test_parse_unknown_update_returns_none() -> None:
    frame = ProtobufMessage().add_bytes(1, b"not a valid update").serialize()
    assert parse_ws_update(frame) is None
