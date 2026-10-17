"""Confirmed Bale sends and retry-safe media preflight failures."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bale_pv_connector.exceptions import BaleRequestUnconfirmedError
from bale_pv_connector.protobuf_wire import ProtobufMessage, grpc_web_frame
from wootify.plugins.bale_pv.adapter import BalePvAdapter
from wootify.plugins.bale_pv.connector import (
    BalePreSendError,
    BalePvConnector,
    BalePvInstanceRuntime,
    _request_rid,
)


def _runtime(connector):
    runtime = BalePvInstanceRuntime("confirmed-test", "989000000000")
    runtime.auth_state = "authenticated"
    runtime.client = AsyncMock()
    connector._instances[runtime.instance_key] = runtime
    return runtime


def _media_client(connector):
    client = AsyncMock()
    cookie = httpx.Response(200)
    upload_url = ProtobufMessage().add_int64(1, 123).add_string(2, "https://storage.test/upload").add_int64(3, 456)
    client.post.side_effect = [cookie, httpx.Response(200, content=grpc_web_frame(upload_url.serialize()))]
    client.put.return_value = httpx.Response(200)
    connector._get_media_http_client = MagicMock(return_value=client)
    connector._session_path = MagicMock(return_value=SimpleNamespace(read_text=lambda: "jwt:test"))
    connector._media_metadata_for_send = MagicMock(return_value=(None, None))
    return client


def test_request_rid_is_stable_scoped_and_fits_bale_int64():
    rid = _request_rid("instance", "123", "cw:42:media:0")
    assert rid == _request_rid("instance", "123", "cw:42:media:0")
    assert 2**62 <= rid < 2**63
    assert rid != _request_rid("instance", "123", "cw:42:media:1")
    assert rid != _request_rid("other-instance", "123", "cw:42:media:0")
    assert rid != _request_rid("instance", "456", "cw:42:media:0")


@pytest.mark.anyio
async def test_confirmed_text_retry_uses_same_rid_and_requires_ack():
    connector = BalePvConnector()
    runtime = _runtime(connector)
    runtime.client.send_message.return_value = b""
    first = await connector.send_text(runtime.instance_key, "123", "hello", mirror_echo=False, idempotency_key="cw:42:text")
    second = await connector.send_text(runtime.instance_key, "123", "hello", mirror_echo=False, idempotency_key="cw:42:text")
    assert first["result"]["rid"] == second["result"]["rid"]
    assert first["result"]["delivery_confirmed"] is True
    assert runtime.client.send_message.await_args.kwargs["random_id"] == first["result"]["rid"]


@pytest.mark.anyio
async def test_successful_storage_upload_without_message_ack_is_not_delivered():
    connector = BalePvConnector()
    runtime = _runtime(connector)
    client = _media_client(connector)
    runtime.client.send_document.return_value = None
    with pytest.raises(BaleRequestUnconfirmedError) as caught:
        await connector.send_media(runtime.instance_key, "123", b"voice", "voice.ogg", idempotency_key="cw:42:media:0")
    assert caught.value.delivery_uncertain is True
    assert caught.value.retryable is False
    client.put.assert_awaited_once()
    assert runtime.message_queue.empty()


@pytest.mark.anyio
async def test_document_ack_returns_confirmed_stable_rid():
    connector = BalePvConnector()
    runtime = _runtime(connector)
    _media_client(connector)
    runtime.client.send_document.return_value = b""
    result = await connector.send_media(runtime.instance_key, "123", b"voice", "voice.ogg", mirror_echo=False, idempotency_key="cw:42:media:0")
    assert result["result"]["rid"] == _request_rid(runtime.instance_key, "123", "cw:42:media:0")
    assert result["result"]["delivery_confirmed"] is True
    assert runtime.message_queue.empty()


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["cookie", "url", "put"])
async def test_media_preflight_transport_failure_is_retryable_without_dispatch(stage):
    connector = BalePvConnector()
    runtime = _runtime(connector)
    client = _media_client(connector)
    error = httpx.ReadTimeout("temporary network failure")
    if stage == "cookie":
        client.post.side_effect = error
    elif stage == "url":
        client.post.side_effect = [httpx.Response(200), error, error, error]
    else:
        client.put.side_effect = error
    with patch("wootify.plugins.bale_pv.connector.asyncio.sleep", new=AsyncMock()):
        with pytest.raises(BalePreSendError) as caught:
            await connector.send_media(runtime.instance_key, "123", b"voice", "voice.ogg")
    assert caught.value.delivery_uncertain is False
    assert caught.value.retryable is True
    runtime.client.send_document.assert_not_awaited()
    assert runtime.message_queue.empty()


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["cookie", "url", "put"])
@pytest.mark.parametrize("status,expected_type", [(503, BalePreSendError), (403, RuntimeError)])
async def test_preflight_http_status_classification(stage, status, expected_type):
    connector = BalePvConnector()
    runtime = _runtime(connector)
    client = _media_client(connector)
    if stage == "cookie":
        client.post.side_effect = [httpx.Response(status)]
    elif stage == "url":
        client.post.side_effect = [httpx.Response(200), httpx.Response(status)]
    else:
        client.put.return_value = httpx.Response(status, content=b"rejected")
    with pytest.raises(expected_type) as caught:
        await connector.send_media(runtime.instance_key, "123", b"voice", "voice.ogg")
    if status == 403:
        assert not isinstance(caught.value, BalePreSendError)
    runtime.client.send_document.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("send_media", [False, True])
async def test_adapter_exposes_confirmed_flag_and_passes_stable_key(send_media):
    adapter = BalePvAdapter("confirmed-test", {})
    method = "send_media" if send_media else "send_text"
    checkpoint = AsyncMock()
    with patch(f"wootify.plugins.bale_pv.adapter.bale_pv.{method}", new=AsyncMock(return_value={"ok": True, "result": {"rid": 123, "delivery_confirmed": True}})) as send:
        if send_media:
            result = await adapter.send_media("123", b"voice", filename="voice.ogg", idempotency_key="cw:42:media:0", before_dispatch=checkpoint)
        else:
            result = await adapter.send_text("123", "hello", idempotency_key="cw:42:text", before_dispatch=checkpoint)
    assert result["confirmed"] is True
    assert send.await_args.kwargs["idempotency_key"] == ("cw:42:media:0" if send_media else "cw:42:text")
    assert send.await_args.kwargs["before_dispatch"] is checkpoint


@pytest.mark.anyio
@pytest.mark.parametrize("send_media", [False, True])
async def test_checkpoint_is_awaited_before_dispatch_after_media_upload(send_media):
    connector = BalePvConnector()
    runtime = _runtime(connector)
    client = _media_client(connector)
    order = []

    async def checkpoint(rid):
        if send_media:
            client.put.assert_awaited_once()
        assert rid == _request_rid(runtime.instance_key, "123", "cw:42:part")
        order.append("checkpoint")

    async def accepted(**_kwargs):
        order.append("send")
        return b""

    runtime.client.send_document.side_effect = accepted
    runtime.client.send_message.side_effect = accepted
    if send_media:
        await connector.send_media(runtime.instance_key, "123", b"voice", "voice.ogg", mirror_echo=False, idempotency_key="cw:42:part", before_dispatch=checkpoint)
    else:
        await connector.send_text(runtime.instance_key, "123", "hello", mirror_echo=False, idempotency_key="cw:42:part", before_dispatch=checkpoint)
    assert order == ["checkpoint", "send"]


@pytest.mark.anyio
@pytest.mark.parametrize("send_media", [False, True])
async def test_failed_durable_checkpoint_blocks_dispatch_and_is_safe_to_retry(send_media):
    connector = BalePvConnector()
    runtime = _runtime(connector)
    _media_client(connector)
    checkpoint = AsyncMock(side_effect=RuntimeError("database unavailable"))
    with pytest.raises(BalePreSendError) as caught:
        if send_media:
            await connector.send_media(runtime.instance_key, "123", b"voice", "voice.ogg", before_dispatch=checkpoint)
        else:
            await connector.send_text(runtime.instance_key, "123", "hello", before_dispatch=checkpoint)
    assert caught.value.delivery_uncertain is False
    runtime.client.send_document.assert_not_awaited()
    runtime.client.send_message.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("send_media", [False, True])
@pytest.mark.parametrize("result", [None, {"ok": True}, {"ok": True, "result": {"rid": 123}}])
async def test_adapter_does_not_promote_unconfirmed_connector_results(send_media, result):
    adapter = BalePvAdapter("confirmed-test", {})
    method = "send_media" if send_media else "send_text"
    with patch(f"wootify.plugins.bale_pv.adapter.bale_pv.{method}", new=AsyncMock(return_value=result)):
        with pytest.raises(BaleRequestUnconfirmedError):
            if send_media:
                await adapter.send_media("123", b"voice", filename="voice.ogg")
            else:
                await adapter.send_text("123", "hello")
