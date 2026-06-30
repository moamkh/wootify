"""Tests for auth protobuf builders and response parsing."""

import pytest

from bale_pv_connector.auth_messages import (
    AuthResult,
    StartPhoneAuthRequest,
    StartPhoneAuthResponse,
    ValidateCodeRequest,
)
from bale_pv_connector.protobuf_wire import ProtobufMessage, ProtobufParser


def test_start_phone_auth_request_serialization() -> None:
    req = StartPhoneAuthRequest(
        phone_number="989123456789",
        app_id=0,
        api_key="KEY",
        device_hash=b"\x00" * 32,
        device_title="test",
        preferred_languages=["fa"],
        send_code_type=0,
    )
    data = req.serialize()
    parsed = ProtobufParser(data).parse()
    assert parsed[1] == [989123456789]
    assert parsed[3] == [b"KEY"]
    assert parsed[5] == [b"test"]
    assert parsed[7] == [b"fa"]


def test_validate_code_request_serialization() -> None:
    req = ValidateCodeRequest(
        transaction_hash="tx123",
        code="123456",
        is_jwt=True,
    )
    data = req.serialize()
    parsed = ProtobufParser(data).parse()
    assert parsed[1] == [b"tx123"]
    assert parsed[2] == [b"123456"]
    # is_jwt is wrapped in BoolValue { value: true }
    bool_wrapper = parsed[3][0]
    bool_parsed = ProtobufParser(bool_wrapper).parse()
    assert bool_parsed[1] == [1]


def test_start_phone_auth_response_parsing() -> None:
    msg = ProtobufMessage()
    msg.add_string(1, "transaction_hash_abc")
    msg.add_int32(2, 1)  # is_registered = true
    msg.add_int32(3, 2)  # activation_type
    msg.add_int32(5, 0)  # sent_code_type

    resp = StartPhoneAuthResponse(msg.serialize())
    assert resp.transaction_hash == "transaction_hash_abc"
    assert resp.is_registered is True
    assert resp.activation_type == 2
    assert resp.sent_code_type == 0


def test_auth_result_parsing() -> None:
    jwt_wrapper = ProtobufMessage().add_string(1, "my.jwt.token")
    msg = ProtobufMessage()
    msg.add_message(2, ProtobufMessage().add_int32(1, 42))  # user raw placeholder
    msg.add_message(4, jwt_wrapper)

    result = AuthResult(msg.serialize())
    assert result.jwt == "my.jwt.token"
    assert result.user_raw is not None
