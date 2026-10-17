"""A write to the transport is not a successful Bale SendMessage RPC."""
from unittest.mock import AsyncMock

import pytest

from bale_pv_connector.exceptions import (
    BaleRequestNotDispatchedError,
    BaleRequestUnconfirmedError,
    BaleRpcError,
)
from bale_pv_connector.messaging_client import BaleMessagingClient
from bale_pv_connector.protobuf_wire import ProtobufMessage
from bale_pv_connector.ws_client import BaleWebSocketClient


@pytest.mark.asyncio
async def test_disconnected_request_is_safe_to_retry():
    client = BaleWebSocketClient()
    with pytest.raises(BaleRequestNotDispatchedError) as caught:
        await client.send_request("messaging", "SendMessage", b"request")
    assert caught.value.retryable is True
    assert caught.value.delivery_uncertain is False
    assert not client._pending_responses


@pytest.mark.asyncio
async def test_failed_dispatch_is_uncertain_and_cleans_pending_request():
    client = BaleWebSocketClient()
    client._connected = True
    client._ws = AsyncMock()
    client._ws.send.side_effect = ConnectionError("connection lost during send")
    with pytest.raises(BaleRequestUnconfirmedError) as caught:
        await client.send_request("messaging", "SendMessage", b"request")
    assert caught.value.retryable is False
    assert caught.value.delivery_uncertain is True
    assert not client._pending_responses


@pytest.mark.asyncio
async def test_ack_timeout_is_not_success_and_cleans_pending_request():
    client = BaleWebSocketClient()
    client._connected = True
    client._ws = AsyncMock()
    with pytest.raises(BaleRequestUnconfirmedError):
        await client.send_request("messaging", "SendMessage", b"request", timeout=0.001)
    assert not client._pending_responses


@pytest.mark.asyncio
async def test_non_send_rpc_preserves_existing_timeout_contract():
    client = BaleWebSocketClient()
    client._connected = True
    client._ws = AsyncMock()
    with pytest.raises(TimeoutError):
        await client.send_request("messaging", "LoadHistory", b"request", timeout=0.001)
    assert not client._pending_responses


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [b"", b"\x08\x01"])
async def test_explicit_success_response_is_confirmed_even_with_empty_payload(response):
    client = BaleWebSocketClient()
    client._connected = True
    client._ws = AsyncMock()

    async def reply(_payload):
        inner = ProtobufMessage()
        inner.add_int64(3, client._req_index)
        # ProtobufMessage omits empty bytes fields, so encode a deliberately
        # present empty response to distinguish it from an absent response.
        payload = b"\x12\x00" if response == b"" else ProtobufMessage().add_bytes(2, response).serialize()
        frame = ProtobufMessage().add_bytes(1, payload + inner.serialize()).serialize()
        await client._handle_message(frame)

    client._ws.send.side_effect = reply
    assert await client.send_request("messaging", "SendMessage", b"request") == response
    assert not client._pending_responses


@pytest.mark.asyncio
async def test_explicit_rpc_rejection_is_not_reported_as_uncertain_success():
    client = BaleWebSocketClient()
    client._connected = True
    client._ws = AsyncMock()

    async def reject(_payload):
        error = ProtobufMessage().add_int32(1, 7).add_string(2, "PermissionDenied")
        inner = ProtobufMessage().add_bytes(1, error.serialize()).add_int64(3, client._req_index)
        await client._handle_message(ProtobufMessage().add_bytes(1, inner.serialize()).serialize())

    client._ws.send.side_effect = reject
    with pytest.raises(BaleRpcError) as caught:
        await client.send_request("messaging", "SendMessage", b"request")
    assert caught.value.code == 7
    assert caught.value.delivery_rejected is True
    assert getattr(caught.value, "delivery_uncertain", False) is False
    assert not client._pending_responses


def test_locally_constructed_rpc_error_does_not_claim_definitive_rejection():
    assert BaleRpcError("no server acknowledgement", code=7).delivery_rejected is False


@pytest.mark.asyncio
async def test_negative_ack_marker_propagates_through_messaging_client():
    client = BaleMessagingClient()
    client.ws = AsyncMock()
    client.ws.send_request.side_effect = BaleRpcError("7: PermissionDenied", code=7, delivery_rejected=True)
    with pytest.raises(BaleRpcError) as caught:
        await client.send_message(123, "hello")
    assert caught.value.delivery_rejected is True
    assert getattr(caught.value, "delivery_uncertain", False) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("send_document", [False, True])
@pytest.mark.parametrize("response", [None, TimeoutError("ack missing")])
async def test_messaging_client_never_turns_missing_ack_into_success(send_document, response):
    client = BaleMessagingClient()
    client.ws = AsyncMock()
    if isinstance(response, Exception):
        client.ws.send_request.side_effect = response
    else:
        client.ws.send_request.return_value = response
    with pytest.raises(BaleRequestUnconfirmedError):
        if send_document:
            await client.send_document(123, 1, 2, 3, "voice.ogg", "audio/ogg")
        else:
            await client.send_message(123, "hello")
