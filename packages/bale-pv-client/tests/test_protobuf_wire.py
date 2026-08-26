"""Tests for the hand-rolled protobuf wire encoder/decoder."""

import pytest

from bale_pv_connector.protobuf_wire import (
    ProtobufMessage,
    ProtobufParser,
    decode_varint,
    encode_varint,
    grpc_web_frame,
    parse_grpc_web_frame,
    parse_grpc_web_response,
)


def test_encode_varint() -> None:
    assert encode_varint(0) == b"\x00"
    assert encode_varint(1) == b"\x01"
    assert encode_varint(150) == b"\x96\x01"


def test_decode_varint() -> None:
    value, pos = decode_varint(b"\x96\x01", 0)
    assert value == 150
    assert pos == 2


def test_roundtrip_varint() -> None:
    for value in (0, 1, 127, 128, 150, 2**32, 2**63 - 1):
        encoded = encode_varint(value)
        decoded, _ = decode_varint(encoded, 0)
        assert decoded == value


def test_protobuf_message_roundtrip() -> None:
    msg = ProtobufMessage()
    msg.add_string(1, "hello")
    msg.add_int64(2, 42)
    msg.add_int32(3, 7)
    msg.add_bool(4, True)
    msg.add_bytes(5, b"raw")

    data = msg.serialize()
    parsed = ProtobufParser(data).parse()

    assert parsed[1] == [b"hello"]
    assert parsed[2] == [42]
    assert parsed[3] == [7]
    assert parsed[4] == [1]
    assert parsed[5] == [b"raw"]


def test_repeated_fields() -> None:
    msg = ProtobufMessage()
    msg.add_repeated_string(1, ["a", "b", "c"])
    msg.add_repeated_int32(2, [10, 20])

    parsed = ProtobufParser(msg.serialize()).parse()
    assert parsed[1] == [b"a", b"b", b"c"]
    assert parsed[2] == [10, 20]


def test_nested_message() -> None:
    inner = ProtobufMessage().add_string(1, "inner")
    outer = ProtobufMessage().add_message(2, inner)
    parsed = ProtobufParser(outer.serialize()).parse()
    assert parsed[2] == [inner.serialize()]


def test_grpc_web_frame_roundtrip() -> None:
    payload = b"test payload"
    framed = grpc_web_frame(payload)
    flags, decoded = parse_grpc_web_frame(framed)
    assert flags == 0
    assert decoded == payload


def test_parse_grpc_web_response_ok() -> None:
    payload = b"payload"
    framed = grpc_web_frame(payload)
    msg, status, message = parse_grpc_web_response(framed)
    assert msg == payload
    assert status == 0
    assert message == ""


def test_parse_grpc_web_response_trailer() -> None:
    payload = b"payload"
    data = grpc_web_frame(payload)
    trailer = b"grpc-status: 5\ngrpc-message: not found\n"
    data += b"\x80" + len(trailer).to_bytes(4, "big") + trailer
    msg, status, message = parse_grpc_web_response(data)
    assert msg == payload
    assert status == 5
    assert message == "not found"
