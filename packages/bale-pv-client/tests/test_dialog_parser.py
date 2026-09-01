"""Tests for dialog/history response parsers."""

from bale_pv_connector.dialog_parser import (
    parse_group,
    parse_load_dialogs_response,
    parse_load_history_response,
    parse_user,
)
from bale_pv_connector.messaging_messages import Peer, TextMessage
from bale_pv_connector.protobuf_wire import ProtobufMessage


def _build_user(uid: int, name: str) -> bytes:
    msg = ProtobufMessage()
    msg.add_int32(1, uid)
    msg.add_string(3, name)
    return msg.serialize()


def _build_group(gid: int, title: str) -> bytes:
    msg = ProtobufMessage()
    msg.add_int32(1, gid)
    msg.add_string(3, title)
    return msg.serialize()


def _build_peer(peer_id: int) -> bytes:
    return Peer(peer_id).serialize()


def test_parse_user() -> None:
    raw = _build_user(42, "Alice")
    parsed = parse_user(raw)
    assert parsed is not None
    assert parsed["id"] == 42
    assert parsed["name"] == "Alice"


def test_parse_group() -> None:
    raw = _build_group(7, "Test Group")
    parsed = parse_group(raw)
    assert parsed is not None
    assert parsed["id"] == 7
    assert parsed["title"] == "Test Group"


def test_parse_load_dialogs_response() -> None:
    response = ProtobufMessage()
    response.add_bytes(1, _build_group(1, "G1"))
    response.add_bytes(2, _build_user(2, "Bob"))

    dialog = ProtobufMessage()
    dialog.add_bytes(1, _build_peer(2))
    dialog.add_int32(2, 3)  # unread_count
    response.add_bytes(3, dialog.serialize())

    parsed = parse_load_dialogs_response(response.serialize())
    assert len(parsed["groups"]) == 1
    assert parsed["groups"][0]["title"] == "G1"
    assert len(parsed["users"]) == 1
    assert parsed["users"][0]["name"] == "Bob"
    assert len(parsed["dialogs"]) == 1
    assert parsed["dialogs"][0]["unread_count"] == 3


def test_parse_load_history_response() -> None:
    response = ProtobufMessage()
    response.add_bytes(2, _build_user(9, "Carol"))

    parsed = parse_load_history_response(response.serialize())
    assert len(parsed["users"]) == 1
    assert parsed["users"][0]["id"] == 9
