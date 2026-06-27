"""
Module Overview
---------------
Purpose: HTTP client wrappers for external APIs used by the bridge.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.utils.logging_utils import redact_secret, truncate_text

logger = logging.getLogger(__name__)


class ChatwootClient:
    """Represents chatwoot client."""
    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        timeout: int = 30,
    ):
        """Initialize the instance."""
        self.base_url = base_url.rstrip("/")
        self.token = (token or "").strip()
        self._client = httpx.AsyncClient(timeout=timeout)

    # -------------------------
    # helpers
    # -------------------------

    def _headers(self) -> Dict[str, str]:
        """Internal helper to headers."""
        headers = {"Accept": "application/json"}
        if self.token:
            headers["api_access_token"] = self.token
        return headers

    def _safe_target(self, path: str) -> str:
        """Internal helper to safe target."""
        safe_token = (
            redact_secret(self.token) if settings.LOG_REDACT_SECRETS else self.token
        )
        return f"{self.base_url}{path}".replace(self.token, safe_token)

    @staticmethod
    def _response_json(response: Optional[httpx.Response]) -> Optional[Dict[str, Any]]:
        """Internal helper to parse a response body as JSON."""
        if response is None:
            return None
        try:
            payload = response.json()
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def extract_inbox_webhook_url(value: Any) -> Optional[str]:
        """Internal helper to extract an inbox callback URL from Chatwoot payloads."""
        queue = [value]
        while queue:
            current = queue.pop(0)
            if not isinstance(current, dict):
                continue

            for key in ('callback_webhook_url', 'webhook_url'):
                candidate = str(current.get(key) or '').strip()
                if candidate:
                    return candidate

            payload = current.get('payload')
            if isinstance(payload, dict):
                queue.append(payload)

            channel = current.get('channel')
            if isinstance(channel, dict):
                queue.append(channel)

        return None

    @classmethod
    def _is_duplicate_phone_validation_error(cls, response: Optional[httpx.Response]) -> bool:
        """Internal helper to identify duplicate-phone validation errors."""
        if response is None or response.status_code != 422:
            return False

        payload = cls._response_json(response) or {}
        message = str(payload.get("message") or "").strip().lower()
        attributes = payload.get("attributes")
        if isinstance(attributes, list):
            normalized_attributes = {str(item).strip().lower() for item in attributes}
        else:
            normalized_attributes = set()

        return (
            "phone number" in message
            and "already been taken" in message
            and "phone_number" in normalized_attributes
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        files: Any = None,
        log_http_error: bool = True,
        retry_on_read_errors: bool = True,
    ) -> Any:
        """Internal helper to request with retry on transient errors."""
        import asyncio

        url = f"{self.base_url}{path}"
        target = self._safe_target(path)

        payload_bytes = (
            len(json.dumps(json_data, ensure_ascii=False).encode())
            if json_data
            else len(str(data).encode())
            if data
            else 0
        )

        logger.info(
            "chatwoot.request start method=%s target=%s payload_bytes=%s",
            method.upper(),
            target,
            payload_bytes,
        )

        start = time.monotonic()
        last_exc: Optional[Exception] = None

        for attempt in range(3):
            try:
                resp = await self._client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json_data,
                    data=data,
                    files=files,
                )

                elapsed = round(time.monotonic() - start, 3)

                logger.info(
                    "chatwoot.request response method=%s status=%s elapsed=%ss",
                    method.upper(),
                    resp.status_code,
                    elapsed,
                )

                # Retry on transient status codes before raising
                if resp.status_code in (429, 502, 503, 504) and attempt < 2:
                    wait = 2 ** attempt
                    logger.warning(
                        "chatwoot.request retry status=%s attempt=%s wait=%ss",
                        resp.status_code,
                        attempt + 1,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()
                if resp.status_code == 204 or not resp.content:
                    return {"ok": True}

                try:
                    payload = resp.json()
                except Exception:
                    payload = {
                        "ok": False,
                        "error_code": resp.status_code,
                        "description": truncate_text(resp.text, 500),
                    }

                # Chatwoot sometimes returns errors inside 200 responses
                if isinstance(payload, dict) and payload.get("error"):
                    logger.error(
                        "chatwoot.api_error target=%s response=%s",
                        target,
                        payload,
                    )

                return payload

            except httpx.ReadTimeout:
                if retry_on_read_errors and attempt < 2:
                    wait = 2 ** attempt
                    logger.warning(
                        "chatwoot.request retry timeout attempt=%s wait=%ss",
                        attempt + 1,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(
                    "chatwoot.request READ TIMEOUT target=%s elapsed=%ss retry=%s",
                    target,
                    round(time.monotonic() - start, 3),
                    retry_on_read_errors,
                )
                raise

            except httpx.ReadError:
                if retry_on_read_errors and attempt < 2:
                    wait = 2 ** attempt
                    logger.warning(
                        "chatwoot.request retry read_error attempt=%s wait=%ss",
                        attempt + 1,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error(
                    "chatwoot.request READ ERROR target=%s elapsed=%ss retry=%s",
                    target,
                    round(time.monotonic() - start, 3),
                    retry_on_read_errors,
                )
                raise

            except httpx.HTTPStatusError as e:
                last_exc = e
                response = e.response
                body = ""
                try:
                    body = truncate_text(response.text or "", 1000) if response is not None else ""
                except Exception:
                    body = ""
                if log_http_error:
                    logger.error(
                        "chatwoot.request HTTP ERROR target=%s status=%s body=%s error=%s",
                        target,
                        response.status_code if response is not None else "unknown",
                        body,
                        str(e),
                        exc_info=True,
                    )
                raise

        # Should never reach here, but satisfy type checker
        if last_exc:
            raise last_exc
        raise RuntimeError("unexpected end of retry loop")

    # -------------------------
    # public api
    # -------------------------

    async def post_message(
        self,
        account_id: int,
        conversation_id: int,
        data: Dict[str, Any],
    ) -> Any:
        """Post message.

        Read timeouts are not retried because the request may have already
        been processed by Chatwoot, which would create a duplicate message.
        """
        return await self._request(
            "POST",
            f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages",
            json_data=data,
            retry_on_read_errors=False,
        )

    async def post_message_with_attachments(
        self,
        account_id: int,
        conversation_id: int,
        data: Dict[str, Any],
        attachments: list[tuple[str, bytes, Optional[str]]],
    ) -> Any:
        """Post message with attachments.

        Read timeouts are not retried because the request body may have
        already been accepted by Chatwoot, which would create a duplicate
        message (especially visible for media/stickers/GIFs).
        """
        files = [
            (
                "attachments[]",
                (filename, content, content_type or "application/octet-stream"),
            )
            for filename, content, content_type in attachments
        ]

        logger.info(
            "chatwoot.attachments account_id=%s conversation_id=%s files=%s",
            account_id,
            conversation_id,
            [f"{f[0]}({len(f[1])}b,{f[2] or 'unknown'})" for f in attachments],
        )

        return await self._request(
            "POST",
            f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages",
            data=data,
            files=files,
            retry_on_read_errors=False,
        )

    async def create_conversation(
        self,
        account_id: int,
        data: Dict[str, Any],
    ) -> Any:
        """Create conversation."""
        return await self._request(
            "POST",
            f"/api/v1/accounts/{account_id}/conversations",
            json_data=data,
        )

    async def toggle_conversation_status(
        self,
        account_id: int,
        conversation_id: int,
        status: str,
    ) -> Any:
        """Toggle conversation status."""
        return await self._request(
            "POST",
            f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/toggle_status",
            json_data={"status": str(status).strip()},
        )

    async def create_contact(
        self,
        account_id: int,
        data: Dict[str, Any],
    ) -> Any:
        """Create contact."""
        return await self._request(
            "POST",
            f"/api/v1/accounts/{account_id}/contacts",
            json_data=data,
        )

    async def update_contact(
        self,
        account_id: int,
        contact_id: int,
        data: Dict[str, Any],
    ) -> Any:
        """Update contact."""
        path = f"/api/v1/accounts/{account_id}/contacts/{contact_id}"
        try:
            return await self._request(
                "PUT",
                path,
                json_data=data,
                log_http_error=False,
            )
        except httpx.HTTPStatusError as exc:
            response = exc.response
            status = response.status_code if response is not None else None
            if self._is_duplicate_phone_validation_error(response):
                logger.info(
                    "chatwoot.update_contact duplicate_phone account_id=%s contact_id=%s status=%s",
                    account_id,
                    contact_id,
                    status,
                )
                raise
            if status in {404, 405, 422}:
                logger.warning(
                    "chatwoot.update_contact put_failed account_id=%s contact_id=%s status=%s retry=PATCH",
                    account_id,
                    contact_id,
                    status,
                )
                return await self._request(
                    "PATCH",
                    path,
                    json_data=data,
                    log_http_error=False,
                )
            raise

    async def update_contact_avatar(
        self,
        account_id: int,
        contact_id: int,
        image_bytes: bytes,
        filename: str = "avatar.jpg",
    ) -> Any:
        """Upload a binary avatar image for a contact.

        Chatwoot expects a multipart/form-data PUT/PATCH with field name ``avatar``.
        """
        path = f"/api/v1/accounts/{account_id}/contacts/{contact_id}"
        files = {
            "avatar": (filename, image_bytes, "image/jpeg"),
        }
        try:
            return await self._request(
                "PUT",
                path,
                files=files,
                log_http_error=False,
            )
        except httpx.HTTPStatusError as exc:
            response = exc.response
            status = response.status_code if response is not None else None
            if status in {404, 405, 422}:
                logger.warning(
                    "chatwoot.update_contact_avatar put_failed account_id=%s contact_id=%s status=%s retry=PATCH",
                    account_id,
                    contact_id,
                    status,
                )
                return await self._request(
                    "PATCH",
                    path,
                    files=files,
                    log_http_error=False,
                )
            raise

    async def get_contact(
        self,
        account_id: int,
        contact_id: int,
    ) -> Any:
        """Get contact."""
        return await self._request(
            "GET",
            f"/api/v1/accounts/{account_id}/contacts/{contact_id}",
        )

    async def search_contacts(
        self,
        account_id: int,
        q: str,
        page: int = 1,
    ) -> Any:
        """Search contacts."""
        return await self._request(
            "GET",
            f"/api/v1/accounts/{account_id}/contacts/search?q={q}&page={page}",
        )

    async def delete_contact(
        self,
        account_id: int,
        contact_id: int,
    ) -> Any:
        """Delete a contact.

        Treats 404 as success: the contact may have already been removed or
        never existed on this Chatwoot account.
        """
        try:
            return await self._request(
                "DELETE",
                f"/api/v1/accounts/{account_id}/contacts/{contact_id}",
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.debug(
                    "chatwoot.delete_contact_not_found account_id=%s contact_id=%s",
                    account_id,
                    contact_id,
                )
                return {"ok": True, "deleted": False, "reason": "not_found"}
            raise

    async def list_contact_conversations(
        self,
        account_id: int,
        contact_id: int,
    ) -> Any:
        """List contact conversations."""
        return await self._request(
            "GET",
            f"/api/v1/accounts/{account_id}/contacts/{contact_id}/conversations",
        )

    async def list_inboxes(self, account_id: int) -> Any:
        """List inboxes."""
        return await self._request(
            "GET",
            f"/api/v1/accounts/{account_id}/inboxes",
        )

    async def create_inbox(
        self,
        account_id: int,
        data: Dict[str, Any],
    ) -> Any:
        """Create inbox."""
        return await self._request(
            "POST",
            f"/api/v1/accounts/{account_id}/inboxes",
            json_data=data,
        )

    async def update_inbox(
        self,
        account_id: int,
        inbox_id: int,
        data: Dict[str, Any],
    ) -> Any:
        """Update inbox."""
        path = f"/api/v1/accounts/{account_id}/inboxes/{inbox_id}"
        try:
            return await self._request(
                "PATCH",
                path,
                json_data=data,
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 405:
                logger.warning(
                    "chatwoot.update_inbox patch_failed account_id=%s inbox_id=%s status=%s retry=PUT",
                    account_id,
                    inbox_id,
                    status,
                )
                return await self._request(
                    "PUT",
                    path,
                    json_data=data,
                )
            raise

    async def delete_message(
        self,
        account_id: int,
        conversation_id: int,
        message_id: int,
    ) -> Any:
        """Delete a message from a conversation."""
        return await self._request(
            "DELETE",
            f"/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages/{message_id}",
        )

    async def close(self) -> None:
        """Close."""
        await self._client.aclose()

