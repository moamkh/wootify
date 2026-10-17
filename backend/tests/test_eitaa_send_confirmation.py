"""Eitaa delivery receipts, safe dispatch checkpoints, and replay boundaries."""
from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from eitaa_cli.errors import EitaaRPCError, EitaaTransportError
from eitaa_cli.config import EitaaSettings
from eitaa_cli.session import SessionProfile
from eitaa_cli.tl.codec import TLCodec

from wootify.plugins.eitaa_pv.adapter import EitaaPvAdapter
from wootify.plugins.eitaa_pv.connector import (
    EitaaPreSendError, EitaaPvConnector, EitaaPvRuntime,
    EitaaSendRejectedError, EitaaSendUnconfirmedError,
    _FINAL_SEND, _StickyEitaaTransport,
)
from wootify.plugins.eitaa_pv.session_client import EitaaRefreshPending, SessionAwareEitaaClient


def _runtime(connector=None):
    client = SimpleNamespace(
        profile=SimpleNamespace(authenticated=True),
        peers=SimpleNamespace(resolve=AsyncMock(return_value={"_": "inputPeerUser", "user_id": 42, "access_hash": 7})),
        invoke=AsyncMock(return_value={"_": "updateShortSentMessage", "id": 77}),
    )
    runtime = EitaaPvRuntime("eitaa-test", "989000000000", None, client)
    runtime.peer_refs["42"] = "user:42:7"
    if connector is not None:
        connector._runtimes[runtime.instance_key] = runtime
    return runtime


def _transport():
    transport = object.__new__(_StickyEitaaTransport)
    transport.settings = SimpleNamespace(endpoints=lambda kind: ["https://one.invalid", "https://two.invalid"])
    transport._selected = {}
    transport._client = AsyncMock()
    transport._codec = TLCodec()
    transport._send_method_ids = {transport._codec.schema.method(method).id & 0xFFFFFFFF for method in ("messages.sendMessage", "messages.sendMedia")}
    return transport


def _wire_payload(codec, method="messages.sendMessage"):
    params = {"peer": {"_": "inputPeerUser", "user_id": 42, "access_hash": 7}, "message": "hello", "random_id": 123}
    if method == "messages.sendMedia":
        params["media"] = {"_": "inputMediaUploadedPhoto", "file": {"_": "inputFile", "id": 1, "parts": 1, "name": "photo.jpg", "md5_checksum": "abc"}}
    return codec.encode_method("eitaaObject", {"token": "test-only", "imei": "device", "packed_data": codec.encode_method(method, {} if method == "updates.getState" else params), "layer": 105, "flags": 0})


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["messages.sendMessage", "messages.sendMedia"])
@pytest.mark.parametrize("error", [httpx.ReadTimeout("response lost"), httpx.WriteError("write interrupted"), httpx.RemoteProtocolError("socket closed")])
async def test_final_send_transport_does_not_fail_over_after_possible_acceptance(method, error):
    transport = _transport()
    transport._client.post.side_effect = [error, httpx.Response(200, content=b"another host")]
    token = _FINAL_SEND.set(True)
    try:
        with pytest.raises(EitaaSendUnconfirmedError) as caught:
            await transport.post(_wire_payload(transport._codec, method), kind="upload" if method.endswith("Media") else "client")
    finally:
        _FINAL_SEND.reset(token)
    assert caught.value.delivery_uncertain is True
    transport._client.post.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("status,body", [(502, b"upstream error"), (200, b"")])
async def test_final_send_http_error_or_empty_body_never_replays(status, body):
    transport = _transport()
    transport._client.post.return_value = httpx.Response(status, content=body)
    token = _FINAL_SEND.set(True)
    try:
        with pytest.raises(EitaaSendUnconfirmedError):
            await transport.post(_wire_payload(transport._codec))
    finally:
        _FINAL_SEND.reset(token)
    transport._client.post.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("error", [httpx.ConnectError("unreachable"), httpx.ConnectTimeout("connect timed out"), httpx.PoolTimeout("pool unavailable")])
async def test_final_send_connect_failures_are_safe_to_retry(error):
    transport = _transport()
    transport._client.post.side_effect = error
    token = _FINAL_SEND.set(True)
    try:
        with pytest.raises(EitaaPreSendError) as caught:
            await transport.post(_wire_payload(transport._codec))
    finally:
        _FINAL_SEND.reset(token)
    assert caught.value.retryable is True
    assert caught.value.delivery_uncertain is False
    transport._client.post.assert_awaited_once()


@pytest.mark.anyio
async def test_read_rpc_inside_send_context_preserves_safe_endpoint_failover():
    transport = _transport()
    transport._client.post.side_effect = [httpx.ReadError("read failed"), httpx.Response(200, content=b"read reply")]
    token = _FINAL_SEND.set(True)
    try:
        assert await transport.post(_wire_payload(transport._codec, "updates.getState")) == b"read reply"
    finally:
        _FINAL_SEND.reset(token)
    assert transport._client.post.await_count == 2


@pytest.mark.anyio
async def test_real_session_client_does_not_hide_an_ambiguous_send_replay(tmp_path):
    transport = _transport()
    transport._client.post.side_effect = [httpx.ReadTimeout("accepted but response lost"), httpx.Response(200, content=b"must not be sent")]
    client = SessionAwareEitaaClient(
        EitaaSettings(session_file=tmp_path / "session.json"), transport=transport,
        _loaded_profile=SessionProfile(name="test", token="test-token", imei="fixed-device", user={"id": 1}),
    )
    connector = EitaaPvConnector()
    runtime = _runtime(connector)
    runtime.client = client
    with pytest.raises(EitaaSendUnconfirmedError):
        await connector.send_text("eitaa-test", "42", "hello", idempotency_key="eitaa-test:42:123:text")
    transport._client.post.assert_awaited_once()


@pytest.mark.anyio
async def test_real_session_client_preserves_auth_recovery_and_same_send_identity(tmp_path):
    transport = _transport()
    codec = transport._codec
    transport._client.post.side_effect = [
        httpx.Response(200, content=codec.encode_constructor("eitaa_updates_expire_token", {})),
        httpx.Response(200, content=codec.encode_constructor("eitaa_updates_token", {"token": "replacement-token", "expire": 3600, "date": 123})),
        httpx.Response(200, content=codec.encode_constructor("updateShortSentMessage", {"id": 77, "pts": 1, "pts_count": 1, "date": 123, "out": True})),
    ]
    client = SessionAwareEitaaClient(
        EitaaSettings(session_file=tmp_path / "session.json"), transport=transport,
        _loaded_profile=SessionProfile(name="test", token="old-token", imei="fixed-device", user={"id": 1}),
    )
    client.store.asave = AsyncMock()
    connector = EitaaPvConnector()
    runtime = _runtime(connector)
    runtime.client = client
    checkpoint = AsyncMock()
    result = await connector.send_text("eitaa-test", "42", "hello", idempotency_key="eitaa-test:42:123:text", before_dispatch=checkpoint)
    assert result["message_id"] == "77" and result["delivery_confirmed"] is True
    checkpoint.assert_awaited_once_with(result["random_id"])
    envelopes = [codec.decode_method_request("eitaaObject", call.kwargs["content"]) for call in transport._client.post.await_args_list]
    assert [item["token"] for item in envelopes] == ["old-token", "old-token", "replacement-token"]
    assert envelopes[0]["packed_data"] == envelopes[2]["packed_data"]
    client.store.asave.assert_awaited_once()


@pytest.mark.parametrize("identifier", [0, -1, True, None, "77"])
def test_confirmation_requires_positive_typed_server_message_id(identifier):
    with pytest.raises(EitaaSendUnconfirmedError):
        EitaaPvConnector._confirmed_send_result({"_": "updateShortSentMessage", "id": identifier}, "42", 123)


def test_generated_random_id_and_bare_message_id_do_not_prove_delivery():
    with pytest.raises(EitaaSendUnconfirmedError):
        EitaaPvConnector._confirmed_send_result({"message_id": 77, "random_id": 123}, "42", 123)


def test_mapping_ack_matches_this_request_not_first_update():
    response = {"_": "updates", "updates": [
        {"_": "updateMessageID", "id": 999, "random_id": 999},
        {"_": "updateMessageID", "id": 77, "random_id": 123},
    ]}
    result = EitaaPvConnector._confirmed_send_result(response, "42", 123)
    assert result["message_id"] == "77"
    assert result["random_id"] == 123
    assert result["delivery_confirmed"] is True


def test_unrelated_mapping_is_not_replaced_by_an_unrelated_native_update():
    response = {"_": "updates", "updates": [
        {"_": "updateMessageID", "id": 999, "random_id": 999},
        {"_": "updateNewMessage", "message": {"id": 77, "out": True, "peer_id": {"_": "peerUser", "user_id": 42}}},
    ]}
    with pytest.raises(EitaaSendUnconfirmedError):
        EitaaPvConnector._confirmed_send_result(response, "42", 123)


@pytest.mark.parametrize("peer_id,outgoing,valid", [(42, True, True), (43, True, False), (42, False, False)])
def test_new_message_ack_is_outgoing_and_target_scoped(peer_id, outgoing, valid):
    response = {"_": "updates", "updates": [{"_": "updateNewMessage", "message": {"id": 77, "out": outgoing, "peer_id": {"_": "peerUser", "user_id": peer_id}}}]}
    if valid:
        assert EitaaPvConnector._confirmed_send_result(response, "42", 123)["message_id"] == "77"
    else:
        with pytest.raises(EitaaSendUnconfirmedError):
            EitaaPvConnector._confirmed_send_result(response, "42", 123)


@pytest.mark.anyio
async def test_text_send_keeps_stable_request_identity_and_checkpoint_before_rpc():
    connector = EitaaPvConnector()
    runtime = _runtime(connector)
    order = []
    request_ids = []

    async def checkpoint(request_id):
        order.append("checkpoint")
        request_ids.append(request_id)

    async def invoke(method, params, **kwargs):
        order.append("send")
        assert params["random_id"] == request_ids[-1]
        return {"_": "updateShortSentMessage", "id": 77}

    runtime.client.invoke.side_effect = invoke
    key = "eitaa-test:42:123:text"
    first = await connector.send_text("eitaa-test", "42", "hello", idempotency_key=key, before_dispatch=checkpoint)
    second = await connector.send_text("eitaa-test", "42", "hello", idempotency_key=key, before_dispatch=checkpoint)
    assert request_ids[0] == request_ids[1] != 77
    assert order == ["checkpoint", "send", "checkpoint", "send"]
    assert first["message_id"] == second["message_id"] == "77"
    assert request_ids[0] == EitaaPvConnector._send_random_id(key)
    assert EitaaPvConnector._scoped_random_id("another-instance", "42", "another-instance:42:123:text") != request_ids[0]
    assert EitaaPvConnector._scoped_random_id("eitaa-test", "43", "eitaa-test:43:123:text") != request_ids[0]


@pytest.mark.anyio
async def test_checkpoint_failure_prevents_final_rpc():
    connector = EitaaPvConnector()
    runtime = _runtime(connector)
    with pytest.raises(EitaaPreSendError):
        await connector.send_text("eitaa-test", "42", "hello", before_dispatch=AsyncMock(side_effect=RuntimeError("database unavailable")))
    runtime.client.invoke.assert_not_awaited()


@pytest.mark.anyio
async def test_peer_lookup_transport_failure_precedes_checkpoint():
    connector = EitaaPvConnector()
    runtime = _runtime(connector)
    runtime.client.peers.resolve.side_effect = EitaaTransportError("lookup failed")
    checkpoint = AsyncMock()
    with pytest.raises(EitaaPreSendError):
        await connector.send_text("eitaa-test", "42", "hello", before_dispatch=checkpoint)
    checkpoint.assert_not_awaited()
    runtime.client.invoke.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("error,expected_type", [
    (EitaaRPCError(403, "CHAT_WRITE_FORBIDDEN"), EitaaSendRejectedError),
    (EitaaRPCError(500, "INTERNAL_SERVER_ERROR"), EitaaSendUnconfirmedError),
    (httpx.ReadError("response lost"), EitaaSendUnconfirmedError),
    (EitaaRefreshPending("renewal backoff"), EitaaPreSendError),
])
async def test_final_rpc_outcome_classification_does_not_replay(error, expected_type):
    connector = EitaaPvConnector()
    runtime = _runtime(connector)
    runtime.client.invoke.side_effect = error
    with pytest.raises(expected_type):
        await connector.send_text("eitaa-test", "42", "hello")
    runtime.client.invoke.assert_awaited_once()


@pytest.mark.anyio
async def test_explicit_flood_wait_is_rejected_but_safe_to_retry_later():
    connector = EitaaPvConnector()
    runtime = _runtime(connector)
    runtime.client.invoke.side_effect = EitaaRPCError(420, "FLOOD_WAIT_30")
    with pytest.raises(EitaaSendRejectedError) as caught:
        await connector.send_text("eitaa-test", "42", "hello")
    assert caught.value.delivery_rejected is True and caught.value.retryable is True
    assert caught.value.retry_after_seconds == 30


class _MediaPath:
    name = "photo.jpg"
    parent = SimpleNamespace(parent=SimpleNamespace(name="not-an-upload-temp-directory"))
    data = b"test image bytes"

    def stat(self):
        return SimpleNamespace(st_size=len(self.data))

    def open(self, mode):
        return BytesIO(self.data)


@pytest.mark.anyio
async def test_media_checkpoint_follows_upload_and_precedes_final_rpc():
    runtime = _runtime()
    order = []

    async def checkpoint(random_id):
        assert random_id == 123
        order.append("checkpoint")

    async def invoke(method, params, **kwargs):
        order.append(method)
        if method.startswith("upload."):
            return True
        assert kwargs["kind"] == "upload"
        return {"_": "updateShortSentMessage", "id": 77}

    runtime.client.invoke.side_effect = invoke
    result = await EitaaPvConnector._send_media_with_checksum(runtime, "user:42:7", _MediaPath(), content_type="image/jpeg", caption="caption", reply_to=None, random_id=123, before_dispatch=checkpoint)
    assert order == ["upload.saveFilePart", "checkpoint", "messages.sendMedia"]
    assert result["message_id"] == "77"
    assert result["random_id"] == 123


@pytest.mark.anyio
async def test_upload_failure_is_retryable_without_final_dispatch():
    runtime = _runtime()
    runtime.client.invoke.side_effect = EitaaTransportError("storage unavailable")
    checkpoint = AsyncMock()
    with pytest.raises(EitaaPreSendError):
        await EitaaPvConnector._send_media_with_checksum(runtime, "user:42:7", _MediaPath(), content_type="image/jpeg", caption="", reply_to=None, random_id=123, before_dispatch=checkpoint)
    checkpoint.assert_not_awaited()
    assert runtime.client.invoke.await_args.args[0] == "upload.saveFilePart"


@pytest.mark.anyio
async def test_temp_cleanup_failure_does_not_hide_a_confirmed_media_send():
    connector = EitaaPvConnector()
    _runtime(connector)
    path = _MediaPath()
    path.parent = SimpleNamespace(parent=SimpleNamespace(name="wootify-eitaa-upload"))
    path.unlink = lambda **kwargs: (_ for _ in ()).throw(OSError("file busy"))
    response = {"_": "updateShortSentMessage", "id": 77, "message_id": "77", "random_id": 123, "delivery_confirmed": True}
    with patch.object(connector, "_media_path", new=AsyncMock(return_value=path)), patch.object(connector, "_send_media_with_checksum", new=AsyncMock(return_value=response)):
        assert (await connector.send_media("eitaa-test", "42", b"image", "photo.jpg"))["delivery_confirmed"] is True


@pytest.mark.anyio
async def test_adapter_contract_exposes_confirmed_server_receipt_and_request_identity():
    adapter = EitaaPvAdapter("eitaa-test", {})
    checkpoint = AsyncMock()
    with patch("wootify.plugins.eitaa_pv.adapter.eitaa_pv.send_text", new=AsyncMock(return_value={"_": "updateShortSentMessage", "id": 77, "random_id": 123})) as send:
        result = await adapter.send_text("42", "hello", idempotency_key="cw:123:text", before_dispatch=checkpoint, mirror_echo=False)
    assert result["ok"] is True and result["confirmed"] is True
    assert result["result"]["message_id"] == "77"
    assert result["result"]["random_id"] == 123
    assert send.await_args.kwargs["before_dispatch"] is checkpoint
    assert send.await_args.kwargs["idempotency_key"] == "cw:123:text"


@pytest.mark.anyio
async def test_difference_exposes_correlation_before_native_echo_and_keeps_it_durable():
    connector = EitaaPvConnector()
    runtime = _runtime(connector)
    runtime.peers_loaded = True
    runtime.web_poll_interval = 5
    runtime.presence_online = True
    runtime.update_state = {"pts": 1, "date": 2, "qts": 0, "seq": 1}
    runtime.client.invoke.return_value = {"_": "updates.difference", "new_messages": [
        {"id": 77, "out": True, "message": "hello", "peer_id": {"_": "peerUser", "user_id": 42}},
    ], "other_updates": [{"_": "updateMessageID", "random_id": 123, "id": 77}],
        "state": {"pts": 2, "date": 3, "qts": 0, "seq": 2}}
    result = await connector.get_updates("eitaa-test")
    confirmation = result["result"][0]
    assert confirmation == {"delivery_confirmation": True, "request_id": "123", "message_id": "77", "platform_message_id": "77", "outgoing": True, "chat_id": "42", "update_id": "eitaa-send-confirmation:123:77"}
    assert (await connector.get_updates("eitaa-test"))["result"][0] == confirmation
    event = EitaaPvAdapter("eitaa-test", {}).normalize_incoming_update(confirmation)
    assert event["delivery_confirmation"] is True
    assert event["request_id"] == "123" and event["platform_message_id"] == "77"


@pytest.mark.anyio
async def test_correlation_without_native_peer_stays_peerless_never_invents_a_contact():
    connector = EitaaPvConnector()
    runtime = _runtime(connector)
    runtime.peers_loaded, runtime.presence_online, runtime.web_poll_interval = True, True, 5
    runtime.update_state = {"pts": 1, "date": 2, "qts": 0, "seq": 1}
    runtime.client.invoke.return_value = {"_": "updates.difference", "other_updates": [{"_": "updateMessageID", "random_id": 123, "id": 77}, {"_": "updateMessageID", "random_id": 0, "id": 0}], "state": {"pts": 2, "date": 3}}
    result = (await connector.get_updates("eitaa-test"))["result"]
    assert len(result) == 1
    assert result[0]["chat_id"] == "" and result[0]["delivery_confirmation"] is True
