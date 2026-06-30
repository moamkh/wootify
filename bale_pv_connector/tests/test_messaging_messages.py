"""Tests for messaging protobuf builders."""

from bale_pv_connector.messaging_messages import (
    Peer,
    SendMessageRequest,
    TextMessage,
    UpdateMessageRequest,
)
from bale_pv_connector.protobuf_wire import ProtobufParser


def test_text_message_serialization() -> None:
    data = TextMessage("hello").serialize()
    parsed = ProtobufParser(data).parse()
    assert parsed[1] == [b"hello"]


def test_send_message_request_serialization() -> None:
    req = SendMessageRequest(
        peer_id=12345,
        text="hi there",
        reply_to_message_id=100,
        random_id=42,
    )
    data = req.serialize()
    parsed = ProtobufParser(data).parse()

    peer_raw = parsed[1][0]
    peer = ProtobufParser(peer_raw).parse()
    assert peer[2] == [12345]

    assert parsed[2] == [42]

    reply_peer_raw = parsed[4][0]
    reply_peer = ProtobufParser(reply_peer_raw).parse()
    assert reply_peer[2] == [100]

    message_raw = parsed[3][0]
    message = ProtobufParser(message_raw).parse()
    text_raw = message[15][0]
    assert ProtobufParser(text_raw).parse()[1] == [b"hi there"]


def test_update_message_request_serialization() -> None:
    req = UpdateMessageRequest(peer_id=1, message_id=99, text="edited")
    data = req.serialize()
    parsed = ProtobufParser(data).parse()
    assert parsed[2] == [99]
