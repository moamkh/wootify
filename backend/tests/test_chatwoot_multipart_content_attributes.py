"""Tests for Commit 3: multipart content_attributes encoding.

Production bug (recurring dead-lettered error on both servers): posting an
edit-as-reply message WITH attachments crashed with

    TypeError: Invalid type for value. Expected primitive type, got
    <class 'dict'>: {'in_reply_to': 42}

because httpx multipart encoding rejects dict values in ``data=``. The fix
flattens nested dicts into Rails bracket notation
(``content_attributes[in_reply_to]``) inside ``post_message_with_attachments``.
"""
from __future__ import annotations

import httpx
import pytest

from wootify.clients.chatwoot_client import ChatwootClient
from wootify.services.chatwoot_bridge_service import chatwoot_bridge


def test_httpx_rejects_dict_values_in_multipart_data():
    """Reproduce the original failure mode: raw httpx multipart encoding with
    a dict value raises the exact TypeError seen in production logs."""
    with pytest.raises(TypeError, match="Invalid type for value"):
        httpx.Request(
            "POST",
            "http://chatwoot/api/v1/accounts/1/conversations/2/messages",
            data={"content_attributes": {"in_reply_to": 42}},
            files=[("attachments[]", ("v.ogg", b"ogg", "audio/ogg"))],
        )


def test_flatten_multipart_data_bracket_notation():
    flatten = ChatwootClient._flatten_multipart_data
    assert flatten(
        {
            "content": "edited : hi",
            "message_type": "incoming",
            "private": False,
            "content_attributes": {"in_reply_to": 42},
        }
    ) == {
        "content": "edited : hi",
        "message_type": "incoming",
        "private": False,
        "content_attributes[in_reply_to]": 42,
    }
    # Primitive values pass through unchanged; empty/missing data is safe.
    assert flatten({"content": "x"}) == {"content": "x"}
    assert flatten({}) == {}
    assert flatten(None) == {}


@pytest.mark.anyio
async def test_edit_as_reply_with_attachment_posts_bracket_notation():
    """End-to-end: the bridge's edit-as-reply payload (content + attachment +
    content_attributes) must encode as a valid multipart request."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.content.decode("utf-8", errors="replace")
        return httpx.Response(200, json={"id": 4242})

    client = ChatwootClient(base_url="http://chatwoot", token="token")
    transport_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await client._client.aclose()
    client._client = transport_client
    try:
        # This is the exact payload shape built by
        # _handle_platform_message_edit_as_reply for an edited voice message.
        reply_data = {
            "content": "edited : سلام",
            "message_type": "incoming",
            "private": False,
            "source_id": "bale:123:edit",
            "content_attributes": {"in_reply_to": 42},
        }
        result = await chatwoot_bridge._post_message_to_chatwoot(
            client,
            1,
            2,
            reply_data,
            [{"content": b"ogg-bytes", "filename": "voice.ogg", "content_type": "audio/ogg"}],
        )
    finally:
        await transport_client.aclose()

    assert result == {"id": 4242}
    assert captured["content_type"].startswith("multipart/form-data")
    body = captured["body"]
    # The nested dict must appear in Rails bracket notation...
    assert 'name="content_attributes[in_reply_to]"' in body
    # ...and the raw dict repr must never leak into the multipart body.
    assert "'in_reply_to'" not in body
    assert "ogg-bytes" in body


@pytest.mark.anyio
async def test_post_message_json_path_keeps_nested_dict():
    """The JSON (non-multipart) path must keep content_attributes nested —
    Chatwoot's JSON API expects a real object, not bracket keys."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode("utf-8", errors="replace")
        return httpx.Response(200, json={"id": 7})

    client = ChatwootClient(base_url="http://chatwoot", token="token")
    transport_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await client._client.aclose()
    client._client = transport_client
    try:
        await client.post_message(
            1, 2, {"content": "hi", "content_attributes": {"in_reply_to": 42}}
        )
    finally:
        await transport_client.aclose()

    assert '"content_attributes":{"in_reply_to":42}' in captured["body"].replace(" ", "")
