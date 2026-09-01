"""Regression tests for BalePvConnector.delete_message.

The connector previously passed a ``just_mine=False`` kwarg that
``BaleMessagingClient.delete_message`` does not accept, so every deletion
propagated from Chatwoot died with a TypeError wrapped in RuntimeError.
"""

from __future__ import annotations

import pytest

from wootify.connectors.bale_pv_connector import BalePvConnector, BalePvInstanceRuntime


@pytest.fixture()
def connector():
    return BalePvConnector()


def _runtime(key: str) -> BalePvInstanceRuntime:
    runtime = BalePvInstanceRuntime(instance_key=key, phone_number="989130000000")
    runtime.auth_state = "authenticated"
    return runtime


@pytest.mark.anyio
async def test_delete_message_calls_client_with_supported_signature(connector):
    """delete_message must call the client with exactly (peer_id, message_ids)."""
    runtime = _runtime("del-test")
    calls = []

    async def _delete_message(peer_id, message_ids, just_mine=False):  # real client signature
        calls.append((peer_id, message_ids, just_mine))
        return b"\x01\x02"

    runtime.client = type("FakeClient", (), {"delete_message": staticmethod(_delete_message)})()
    connector._instances["del-test"] = runtime
    try:
        result = await connector.delete_message("del-test", "12345", "6789012345678901234")
    finally:
        connector._instances.pop("del-test", None)

    assert result["ok"] is True
    assert calls == [(12345, [6789012345678901234], False)]


def test_delete_message_request_revokes_for_everyone():
    """Bale requires the present ``justMine=false`` wrapper for global delete."""
    from bale_pv_connector.messaging_messages import DeleteMessageRequest
    from bale_pv_connector.protobuf_wire import ProtobufParser

    request = DeleteMessageRequest(peer_id=12345, message_ids=[67890]).serialize()
    fields = ProtobufParser(request).parse()

    assert set(fields) == {1, 2, 4}
    assert fields[4] == [b""]


@pytest.mark.anyio
async def test_delete_message_requires_authentication(connector):
    runtime = _runtime("del-unauth")
    runtime.auth_state = "anonymous"
    connector._instances["del-unauth"] = runtime
    try:
        with pytest.raises(RuntimeError, match="not authenticated"):
            await connector.delete_message("del-unauth", "12345", "678")
    finally:
        connector._instances.pop("del-unauth", None)
