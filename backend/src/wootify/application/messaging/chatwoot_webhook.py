"""Configure per-instance Chatwoot account webhooks used by Wootify."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from wootify.application.instances.service import InstanceService
from wootify.infrastructure.chatwoot.client import ChatwootClient


class ChatwootWebhookService:
    """Own the account-webhook lifecycle for a Wootify instance."""

    SUBSCRIPTIONS = [
        "conversation_status_changed",
        "conversation_updated",
        "conversation_created",
        "contact_created",
        "contact_updated",
        "message_created",
        "message_updated",
        "webwidget_triggered",
        "inbox_created",
        "inbox_updated",
        "conversation_typing_on",
        "conversation_typing_off",
    ]

    def __init__(
        self,
        instance_service: InstanceService | None = None,
        client_factory: Callable[..., ChatwootClient] = ChatwootClient,
    ) -> None:
        self._instances = instance_service or InstanceService()
        self._client_factory = client_factory

    async def get_status(self, db: Session, instance_key: str) -> dict[str, Any]:
        """Return whether the exact per-instance webhook already exists."""
        runtime, client, account_id, webhook_url = self._resolve(db, instance_key)
        try:
            webhooks = self._extract_webhooks(await client.list_webhooks(account_id))
            existing = self._find_by_url(webhooks, webhook_url)
            configured = bool(
                existing
                and set(self.SUBSCRIPTIONS).issubset(set(existing.get("subscriptions") or []))
            )
            return self._result(configured, False, existing, webhook_url)
        finally:
            await client.close()

    async def configure(self, db: Session, instance_key: str) -> dict[str, Any]:
        """Create or update the instance webhook with all supported events."""
        runtime, client, account_id, webhook_url = self._resolve(db, instance_key)
        del runtime
        try:
            webhooks = self._extract_webhooks(await client.list_webhooks(account_id))
            existing = self._find_by_url(webhooks, webhook_url)
            webhook_data = {
                "name": f"Wootify: {instance_key}",
                "url": webhook_url,
                "subscriptions": self.SUBSCRIPTIONS,
            }
            if existing:
                response = await client.update_webhook(account_id, int(existing["id"]), webhook_data)
                webhook = self._extract_webhook(response) or {**existing, **webhook_data}
                created = False
            else:
                response = await client.create_webhook(account_id, webhook_data)
                webhook = self._extract_webhook(response) or webhook_data
                created = True
            return self._result(True, created, webhook, webhook_url)
        finally:
            await client.close()

    def _resolve(
        self,
        db: Session,
        instance_key: str,
    ) -> tuple[Any, ChatwootClient, int, str]:
        runtime = self._instances.get_runtime_instance(db, instance_key)
        if runtime is None:
            raise ValueError("instance not found")
        config = runtime.chatwoot
        base_url = str(config.get("base_url") or "").strip()
        token = str(config.get("api_access_token") or "").strip()
        account_id = config.get("account_id")
        webhook_url = str(config.get("webhook_url") or "").strip()
        if not webhook_url:
            from wootify.config import settings

            webhook_url = (
                f"{settings.SERVER_BASE_URL.rstrip('/')}"
                f"/api/v1/webhooks/chatwoot/{instance_key}"
            )
        if not base_url or not token or account_id is None:
            raise ValueError("Chatwoot base URL, API token, and account ID are required")
        return runtime, self._client_factory(base_url=base_url, token=token), int(account_id), webhook_url

    @staticmethod
    def _extract_webhooks(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        value = payload.get("payload")
        if isinstance(value, dict):
            value = value.get("webhooks")
        if not isinstance(value, list):
            value = payload.get("webhooks")
        return [item for item in (value or []) if isinstance(item, dict)]

    @staticmethod
    def _extract_webhook(payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        for value in (payload.get("webhook"), payload.get("payload"), payload):
            if isinstance(value, dict) and value.get("id") is not None:
                return value
        return None

    @staticmethod
    def _find_by_url(webhooks: list[dict[str, Any]], webhook_url: str) -> dict[str, Any] | None:
        return next((item for item in webhooks if str(item.get("url") or "").rstrip("/") == webhook_url.rstrip("/")), None)

    def _result(
        self,
        configured: bool,
        created: bool,
        webhook: dict[str, Any] | None,
        webhook_url: str,
    ) -> dict[str, Any]:
        return {
            "configured": configured,
            "created": created,
            "webhook_id": webhook.get("id") if webhook else None,
            "webhook_url": webhook_url,
            "subscriptions": list(webhook.get("subscriptions") or []) if webhook else [],
        }
