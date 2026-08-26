"""Tests for Commit 2: echo media download resilience.

- ``download_file_by_id`` retries transient file-gateway failures (5xx) with
  a bounded backoff instead of hollowing out the message on the first 502.
- ``_normalize_with_adapter`` raises when every attachment download failed,
  routing the update through the retry/dead-letter path instead of posting a
  hollow message.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import wootify.services.bale_polling_service as polling_module
from wootify.connectors.bale_pv_connector import BalePvConnector, BalePvInstanceRuntime
from wootify.services.bale_polling_service import BalePollingService


# ---------------------------------------------------------------------------
# _normalize_with_adapter: refuse hollow delivery
# ---------------------------------------------------------------------------


def _service_with_adapter(adapter):
    service = BalePollingService()
    runtime = SimpleNamespace(adapter=adapter)
    return service, runtime


@pytest.mark.anyio
async def test_normalize_raises_when_all_attachment_downloads_failed():
    adapter = MagicMock()
    adapter.normalize_incoming_update.return_value = {
        "chat_id": "12345",
        "message_id": "999",
        "attachments": [{"file_id": "x", "filename": "voice.ogg"}],
    }
    adapter.resolve_attachments = AsyncMock(return_value=[])

    service, runtime = _service_with_adapter(adapter)
    with patch("wootify.runtime_registry.get_runtime", return_value=runtime):
        with pytest.raises(RuntimeError, match="refusing hollow delivery"):
            await service._normalize_with_adapter("inst", {"update_id": 1})


@pytest.mark.anyio
async def test_normalize_raises_when_resolve_attachments_throws():
    adapter = MagicMock()
    adapter.normalize_incoming_update.return_value = {
        "chat_id": "12345",
        "message_id": "999",
        "attachments": [{"file_id": "x", "filename": "voice.ogg"}],
    }
    adapter.resolve_attachments = AsyncMock(side_effect=RuntimeError("boom"))

    service, runtime = _service_with_adapter(adapter)
    with patch("wootify.runtime_registry.get_runtime", return_value=runtime):
        with pytest.raises(RuntimeError, match="attachment resolution failed"):
            await service._normalize_with_adapter("inst", {"update_id": 1})


@pytest.mark.anyio
async def test_normalize_delivers_partial_downloads():
    """Only fully-failed attachment sets are retried; partial success delivers."""
    adapter = MagicMock()
    adapter.normalize_incoming_update.return_value = {
        "chat_id": "12345",
        "message_id": "999",
        "attachments": [
            {"file_id": "x", "filename": "a.ogg"},
            {"file_id": "y", "filename": "b.ogg"},
        ],
    }
    adapter.resolve_attachments = AsyncMock(
        return_value=[{"filename": "a.ogg", "data": b"123"}]
    )

    service, runtime = _service_with_adapter(adapter)
    with patch("wootify.runtime_registry.get_runtime", return_value=runtime):
        event = await service._normalize_with_adapter("inst", {"update_id": 1})
    assert event is not None
    assert len(event["attachments"]) == 1


# ---------------------------------------------------------------------------
# download_file_by_id: bounded retry on transient 5xx
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status_code: int, content: bytes = b"", content_type: str = ""):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type} if content_type else {}


class _FakeMediaClient:
    def __init__(self, get_responses):
        self._get_responses = list(get_responses)
        self.get_calls = 0

    async def post(self, url, **kwargs):
        # set-cookie + GetNasimFileUrl calls all "succeed"; the actual parsing
        # is patched out in the test.
        return _Resp(200, b"body")

    async def get(self, url, **kwargs):
        self.get_calls += 1
        return self._get_responses.pop(0)


def _connector_with_runtime(tmp_path, monkeypatch, get_responses):
    connector = BalePvConnector()
    runtime = BalePvInstanceRuntime(instance_key="dl-test", phone_number="989130000000")
    runtime.auth_state = "authenticated"
    connector._instances["dl-test"] = runtime

    session_file = tmp_path / "session.txt"
    session_file.write_text("jwt:test-token")
    monkeypatch.setattr(BalePvConnector, "_session_path", lambda self, inst: session_file)

    media_client = _FakeMediaClient(get_responses)
    monkeypatch.setattr(
        BalePvConnector, "_get_media_http_client", lambda self, inst: media_client
    )
    monkeypatch.setattr(
        BalePvConnector,
        "_extract_url_from_nasim_response",
        staticmethod(lambda msg: "https://next-file-gw.bale.ai/file"),
    )
    monkeypatch.setattr(
        "wootify.connectors.bale_pv_connector.FILE_DOWNLOAD_RETRY_BACKOFF_SECONDS", 0
    )
    # The gRPC-Web envelope parsing is patched out: the fake POST bodies are
    # not real grpc-web frames.
    monkeypatch.setattr(
        "bale_pv_connector.protobuf_wire.parse_grpc_web_response",
        lambda content: (b"msg", 0, ""),
    )
    composite = json.dumps(
        {"file_id": 1, "access_hash": 2, "peer_id": 3, "file_name": "voice.ogg"}
    )
    return connector, media_client, composite


@pytest.mark.anyio
async def test_download_retries_transient_502_then_succeeds(tmp_path, monkeypatch):
    connector, media_client, composite = _connector_with_runtime(
        tmp_path,
        monkeypatch,
        [_Resp(502), _Resp(502), _Resp(200, b"audio-bytes", "audio/ogg")],
    )
    content, ctype, name = await connector.download_file_by_id("dl-test", composite)
    assert content == b"audio-bytes"
    assert ctype == "audio/ogg"
    assert name == "voice.ogg"
    assert media_client.get_calls == 3


@pytest.mark.anyio
async def test_download_gives_up_after_bounded_attempts(tmp_path, monkeypatch):
    connector, media_client, composite = _connector_with_runtime(
        tmp_path,
        monkeypatch,
        [_Resp(502), _Resp(503), _Resp(500), _Resp(200, b"late")],
    )
    content, ctype, name = await connector.download_file_by_id("dl-test", composite)
    assert content == b""
    assert ctype is None
    # Bounded at FILE_DOWNLOAD_MAX_ATTEMPTS (3): the 4th response is never used.
    assert media_client.get_calls == 3


@pytest.mark.anyio
async def test_download_does_not_retry_permanent_404(tmp_path, monkeypatch):
    connector, media_client, composite = _connector_with_runtime(
        tmp_path, monkeypatch, [_Resp(404), _Resp(200, b"never")]
    )
    content, _, _ = await connector.download_file_by_id("dl-test", composite)
    assert content == b""
    assert media_client.get_calls == 1
