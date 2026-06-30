"""
Minimal Protobuf Wire-Format Encoder/Decoder
=============================================

This module implements a lightweight protobuf wire-format serializer/deserializer
without requiring .proto files or generated code.

Built from field definitions extracted from web.bale.ai JS bundle.

Wire Types
----------
0 = varint (int32, int64, uint32, uint64, sint32, sint64, bool, enum)
1 = fixed64 (fixed64, sfixed64, double)
2 = length-delimited (string, bytes, embedded messages, packed repeated)
5 = fixed32 (fixed32, sfixed32, float)

Field Tags
----------
tag = (field_number << 3) | wire_type
"""

import struct
from typing import Any, Dict, List, Optional, Tuple, Union


def encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    result = bytearray()
    while value > 127:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def decode_varint(data: bytes, pos: int) -> Tuple[int, int]:
    """Decode a varint from data at position. Returns (value, new_pos)."""
    result = 0
    shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result, pos


def make_tag(field_number: int, wire_type: int) -> bytes:
    return encode_varint((field_number << 3) | wire_type)


def decode_tag(data: bytes, pos: int) -> Tuple[int, int, int]:
    """Decode tag. Returns (field_number, wire_type, new_pos)."""
    tag, pos = decode_varint(data, pos)
    return tag >> 3, tag & 0x07, pos


class ProtobufMessage:
    """Dynamic protobuf message builder."""

    def __init__(self):
        self._fields: List[Tuple[int, int, Any]] = []

    def add_string(self, field_number: int, value: str) -> "ProtobufMessage":
        if value:
            self._fields.append((field_number, 2, value.encode("utf-8")))
        return self

    def add_bytes(self, field_number: int, value: bytes) -> "ProtobufMessage":
        if value:
            self._fields.append((field_number, 2, value))
        return self

    def add_int32(self, field_number: int, value: int) -> "ProtobufMessage":
        if value != 0:
            self._fields.append((field_number, 0, encode_varint(value)))
        return self

    def add_int64(self, field_number: int, value: int) -> "ProtobufMessage":
        if value != 0:
            # int64 uses the same varint encoding
            self._fields.append((field_number, 0, encode_varint(value)))
        return self

    def add_bool(self, field_number: int, value: bool) -> "ProtobufMessage":
        if value:
            self._fields.append((field_number, 0, b"\x01"))
        return self

    def add_message(self, field_number: int, message: "ProtobufMessage") -> "ProtobufMessage":
        encoded = message.serialize()
        if encoded:
            self._fields.append((field_number, 2, encoded))
        return self

    def add_repeated_string(self, field_number: int, values: List[str]) -> "ProtobufMessage":
        for value in values:
            self.add_string(field_number, value)
        return self

    def add_repeated_int32(self, field_number: int, values: List[int]) -> "ProtobufMessage":
        for value in values:
            self.add_int32(field_number, value)
        return self

    def add_packed_int64(self, field_number: int, values: List[int]) -> "ProtobufMessage":
        """Add a packed repeated int64 field (wire type 2 with packed varints)."""
        if values:
            packed = bytearray()
            for v in values:
                packed.extend(encode_varint(v))
            self._fields.append((field_number, 2, bytes(packed)))
        return self

    def add_packed_int32(self, field_number: int, values: List[int]) -> "ProtobufMessage":
        """Add a packed repeated int32 field (wire type 2 with packed varints)."""
        if values:
            packed = bytearray()
            for v in values:
                packed.extend(encode_varint(v))
            self._fields.append((field_number, 2, bytes(packed)))
        return self

    def serialize(self) -> bytes:
        result = bytearray()
        for field_number, wire_type, value in self._fields:
            result.extend(make_tag(field_number, wire_type))
            if wire_type == 2:
                result.extend(encode_varint(len(value)))
                result.extend(value)
            elif wire_type == 0:
                result.extend(value)
            elif wire_type == 1:
                result.extend(value)  # 8 bytes
            elif wire_type == 5:
                result.extend(value)  # 4 bytes
        return bytes(result)


class ProtobufParser:
    """Parse protobuf wire-format data into a dictionary."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def parse(self) -> Dict[int, List[Any]]:
        """Parse all fields. Returns dict mapping field_number -> list of values."""
        result: Dict[int, List[Any]] = {}
        while self.pos < len(self.data):
            field_number, wire_type, self.pos = decode_tag(self.data, self.pos)

            if wire_type == 0:  # varint
                value, self.pos = decode_varint(self.data, self.pos)
                result.setdefault(field_number, []).append(value)

            elif wire_type == 1:  # fixed64
                value = self.data[self.pos : self.pos + 8]
                self.pos += 8
                result.setdefault(field_number, []).append(value)

            elif wire_type == 2:  # length-delimited
                length, self.pos = decode_varint(self.data, self.pos)
                value = self.data[self.pos : self.pos + length]
                self.pos += length
                result.setdefault(field_number, []).append(value)

            elif wire_type == 5:  # fixed32
                value = self.data[self.pos : self.pos + 4]
                self.pos += 4
                result.setdefault(field_number, []).append(value)

            elif wire_type == 3:  # start_group (proto2 groups)
                # Groups are delimited by end_group (wire_type 4)
                # Skip until we find matching end_group
                group_depth = 1
                while group_depth > 0 and self.pos < len(self.data):
                    tag, self.pos = decode_varint(self.data, self.pos)
                    wt = tag & 0x07
                    if wt == 3:
                        group_depth += 1
                    elif wt == 4:
                        group_depth -= 1
                result.setdefault(field_number, []).append(b"[group]")
            elif wire_type == 4:  # end_group
                result.setdefault(field_number, []).append(b"[end_group]")
            else:
                raise ValueError(f"Unknown wire type: {wire_type}")

        return result


def grpc_web_frame(message: bytes) -> bytes:
    """Wrap protobuf message in gRPC-Web frame."""
    return b"\x00" + struct.pack(">I", len(message)) + message


def parse_grpc_web_frame(data: bytes) -> Tuple[int, bytes]:
    """Parse gRPC-Web frame. Returns (flags, message)."""
    if len(data) < 5:
        return 0, b""
    flags = data[0]
    length = struct.unpack(">I", data[1:5])[0]
    return flags, data[5 : 5 + length]


def parse_grpc_web_response(data: bytes) -> Tuple[bytes, int, str]:
    """
    Parse a full gRPC-Web response.
    Returns (protobuf_message, grpc_status, grpc_message).
    """
    # gRPC-Web response may contain multiple frames:
    # - Data frames (flag 0x00)
    # - Trailer frames (flag 0x80) with grpc-status and grpc-message

    messages = []
    pos = 0
    grpc_status = 0
    grpc_message = ""

    while pos < len(data):
        if pos + 5 > len(data):
            break
        flags = data[pos]
        length = struct.unpack(">I", data[pos + 1 : pos + 5])[0]
        payload = data[pos + 5 : pos + 5 + length]
        pos += 5 + length

        if flags == 0x00:
            messages.append(payload)
        elif flags == 0x80:
            # Trailer frame - parse as HTTP/2-style headers
            for line in payload.split(b"\n"):
                if line.startswith(b"grpc-status:"):
                    grpc_status = int(line.split(b":", 1)[1].strip())
                elif line.startswith(b"grpc-message:"):
                    grpc_message = line.split(b":", 1)[1].strip().decode()

    return b"".join(messages), grpc_status, grpc_message
