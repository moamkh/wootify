"""Tests for per-instance Chatwoot account webhook configuration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from wootify.application.messaging.chatwoot_webhook import ChatwootWebhookService
from wootify.application.messaging.bridge import BridgeService
from wootify.config import settings


def build_service(webhooks):
    instance_service = MagicMock()
    instance_service.get_runtime_instance.return_value = SimpleNamespace(
        chatwoot={
            "base_url": "http://chatwoot",
            "api_access_token": "token",
            "account_id": 1,
        }
    )
    client = AsyncMock()
    client.list_webhooks.return_value = {"payload": {"webhooks": webhooks}}
    factory = MagicMock(return_value=client)
    return ChatwootWebhookService(instance_service, factory), client


@pytest.mark.anyio
async def test_configure_creates_missing_instance_webhook():
    service, client = build_service([])
    client.create_webhook.return_value = {"id": 9}

    result = await service.configure(MagicMock(), "bale-one")

    assert result["configured"] is True
    assert result["created"] is True
    payload = client.create_webhook.await_args.args[1]
    assert payload["url"].endswith("/api/v1/webhooks/chatwoot/bale-one")
    assert "message_updated" in payload["subscriptions"]
    client.close.assert_awaited_once()


@pytest.mark.anyio
async def test_configure_updates_matching_instance_webhook():
    webhook_url = f"{settings.SERVER_BASE_URL.rstrip('/')}/api/v1/webhooks/chatwoot/bale-one"
    service, client = build_service([{"id": 7, "url": webhook_url, "subscriptions": ["message_created"]}])
    client.update_webhook.return_value = {"id": 7, "url": webhook_url}

    result = await service.configure(MagicMock(), "bale-one")

    assert result["created"] is False
    client.update_webhook.assert_awaited_once()
    assert "message_updated" in client.update_webhook.await_args.args[2]["subscriptions"]
    client.close.assert_awaited_once()


@pytest.mark.anyio
async def test_create_or_link_inbox_prefers_configured_id_over_stale_name():
    service = BridgeService()
    runtime = SimpleNamespace(
        chatwoot={
            "base_url": "http://chatwoot",
            "api_access_token": "token",
            "account_id": 1,
            "inbox_id": 4,
            "inbox_name": "Renamed inbox",
        },
        instance=MagicMock(),
    )
    client = AsyncMock()
    client.list_inboxes.return_value = {
        "payload": [{"id": 4, "name": "Old name", "channel": {"webhook_url": "http://old"}}]
    }
    client.update_inbox.return_value = {"id": 4, "name": "Renamed inbox"}
    service._require_runtime_instance = MagicMock(return_value=runtime)
    service._get_chatwoot_client = MagicMock(return_value=client)
    db = MagicMock()

    result = await service.create_chatwoot_inbox(db, "bale-one")

    assert result["created"] is False
    assert result["inbox_id"] == 4
    client.create_inbox.assert_not_awaited()
    client.update_inbox.assert_awaited_once()
