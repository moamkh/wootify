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


def _build_channel_message_frame(
    peer_id: int,
    message_id: int,
    text: str,
    peer_type: int = Peer.PEER_TYPE_USER,
    sender_info: bytes | None = None,
    edited: bool = False,
    channel_peer_id: int | None = None,
    channel_peer_type: int = Peer.PEER_TYPE_USER,
) -> bytes:
    """Build a WebSocket update frame for a channel/group/private edit message (field 162)."""
    peer = Peer(peer_id, peer_type).serialize()
    message = _build_text_message(text)

    channel_message = ProtobufMessage()
    channel_message.add_bytes(1, peer)
    channel_message.add_int64(2, message_id)
    channel_message.add_bytes(3, message)
    if sender_info is not None:
        channel_message.add_bytes(4, sender_info)
    # Real channel/group messages carry a varint date. Edited messages leave
    # field 5 unset (or send nested bytes), which the parser treats as the
    # edit marker together with the sender_info contents.
    if not edited:
        channel_message.add_int64(5, 1784128179040)
    if peer_type in (Peer.PEER_TYPE_GROUP, Peer.PEER_TYPE_CHANNEL):
        channel_message.add_bytes(9, peer)
    elif channel_peer_id is not None:
        # Private edits sometimes include a channel_peer referencing the self user.
        channel_message.add_bytes(9, Peer(channel_peer_id, channel_peer_type).serialize())

    wrapper = ProtobufMessage()
    wrapper.add_bytes(BaleUpdateType.CHANNEL_MESSAGE, channel_message.serialize())

    container = ProtobufMessage()
    container.add_bytes(1, wrapper.serialize())
    container.add_int64(4, 1784128179040)

    inner = ProtobufMessage()
    inner.add_bytes(1, container.serialize())

    outer = ProtobufMessage()
    outer.add_bytes(2, inner.serialize())

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


def test_parse_channel_message_update() -> None:
    """Real channel/group messages carry senderInfo with the actual sender uid."""
    sender_info = ProtobufMessage()
    sender_info.add_int64(1, 987654321)
    frame = _build_channel_message_frame(
        peer_id=1678156035,
        message_id=9163593588334364845,
        text="group hello",
        peer_type=Peer.PEER_TYPE_GROUP,
        sender_info=sender_info.serialize(),
    )
    parsed = parse_ws_update(frame)
    assert parsed is not None
    assert parsed["type"] == "channel_message"
    assert parsed["sender_uid"] == 987654321
    assert parsed["message_id"] == "9163593588334364845"
    assert parsed["peer"] == {"type": 2, "id": 1678156035}
    assert parsed["text"] == "group hello"
    assert parsed.get("edited") is not True


def test_parse_channel_message_edit_update() -> None:
    """Edited group/channel messages carry the original date in senderInfo."""
    sender_info = ProtobufMessage()
    sender_info.add_int64(1, 1784128171355)
    frame = _build_channel_message_frame(
        peer_id=1678156035,
        message_id=9163593588334364845,
        text="mew mew",
        peer_type=Peer.PEER_TYPE_GROUP,
        sender_info=sender_info.serialize(),
        edited=True,
    )
    parsed = parse_ws_update(frame)
    assert parsed is not None
    assert parsed["type"] == "channel_message"
    assert parsed["sender_uid"] == 1678156035
    assert parsed["message_id"] == "9163593588334364845"
    assert parsed["peer"] == {"type": 2, "id": 1678156035}
    assert parsed["text"] == "mew mew"
    assert parsed.get("edited") is True
    assert parsed.get("original_date") == 1784128171355


def test_parse_private_edit_ignores_channel_peer_self() -> None:
    """Edited private messages must use field-1 peer, not channel_peer (self)."""
    sender_info = ProtobufMessage()
    sender_info.add_int64(1, 1784130143424)
    frame = _build_channel_message_frame(
        peer_id=1755271951,
        message_id=6753639098130093411,
        text="tessssstttttttt edited",
        peer_type=Peer.PEER_TYPE_USER,
        sender_info=sender_info.serialize(),
        edited=True,
        channel_peer_id=1654537797,  # self user id, must not become chat_id/sender
    )
    parsed = parse_ws_update(frame)
    assert parsed is not None
    assert parsed["type"] == "channel_message"
    # sender_uid must be the chat partner from field 1, not the self id from channel_peer
    assert parsed["sender_uid"] == 1755271951
    assert parsed["message_id"] == "6753639098130093411"
    assert parsed["peer"] == {"type": 1, "id": 1755271951}
    assert parsed["text"] == "tessssstttttttt edited"
    assert parsed.get("edited") is True


def test_parse_unknown_update_returns_none() -> None:
    frame = ProtobufMessage().add_bytes(1, b"not a valid update").serialize()
    assert parse_ws_update(frame) is None
