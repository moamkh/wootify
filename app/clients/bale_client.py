"""
Module Overview
---------------
Purpose: HTTP client wrappers for external APIs used by the bridge.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.utils.logging_utils import redact_secret, truncate_text


logger = logging.getLogger(__name__)


class BaleClient:
    """Represents bale client."""
    def __init__(
        self,
        api_base_url: str = "https://tapi.bale.ai",
        file_base_url: str = "https://tapi.bale.ai/file",
        timeout: int = 30,
    ):
        """Initialize the instance."""
        self.api_base_url = api_base_url.rstrip("/")
        self.file_base_url = file_base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    def _api_url(self, token: str, method: str) -> str:
        """Internal helper to api url."""
        token = (token or "").strip()
        method = (method or "").strip().lstrip("/")
        return f"{self.api_base_url}/bot{token}/{method}"

    def _safe_api_target(self, token: str, method: str) -> str:
        """Internal helper to safe api target."""
        safe_token = (
            redact_secret(token) if settings.LOG_REDACT_SECRETS else str(token or "")
        )
        return f"{self.api_base_url}/bot{safe_token}/{method.strip().lstrip('/')}"

    def file_url(self, token: str, file_path: str) -> str:
        """File url."""
        token = (token or "").strip()
        file_path = (file_path or "").lstrip("/")
        return f"{self.file_base_url}/bot{token}/{file_path}"

    async def get_me(self, token: str) -> Dict[str, Any]:
        """Get me."""
        logger.debug("bale.getMe target=%s", self._safe_api_target(token, "getMe"))
        resp = await self._client.get(self._api_url(token, "getMe"))
        resp.raise_for_status()
        return resp.json()

    async def send_message(
        self, token: str, chat_id: str, text: str, **params: Any
    ) -> Dict[str, Any]:
        """Send message."""
        payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
        payload.update({k: v for k, v in params.items() if v is not None})

        logger.info(
            "bale.sendMessage chat_id=%s text_len=%s target=%s",
            chat_id,
            len(text or ""),
            self._safe_api_target(token, "sendMessage"),
        )

        try:
            resp = await self._client.post(
                self._api_url(token, "sendMessage"), data=payload
            )
            data = resp.json()

            if not data.get("ok"):
                logger.error(
                    "bale.sendMessage api_error chat_id=%s response=%s",
                    chat_id,
                    data,
                )
            return data

        except Exception as e:
            logger.error(
                "bale.sendMessage failed chat_id=%s error=%s",
                chat_id,
                str(e),
                exc_info=True,
            )
            raise

    async def _send_binary(
        self,
        token: str,
        method: str,
        file_field: str,
        chat_id: str,
        filename: str,
        content: bytes,
        caption: Optional[str] = None,
        **params: Any,
    ) -> Dict[str, Any]:
        """Internal helper to send binary."""
        data: Dict[str, Any] = {"chat_id": chat_id}
        if caption is not None:
            data["caption"] = caption
        data.update({k: v for k, v in params.items() if v is not None})
        files = {file_field: (filename, content, "application/octet-stream")}
        logger.info(
            "bale.%s chat_id=%s filename=%s bytes=%s caption_len=%s target=%s",
            method,
            chat_id,
            filename,
            len(content or b""),
            len(caption or ""),
            self._safe_api_target(token, method),
        )
        try:
            resp = await self._client.post(
                self._api_url(token, method), data=data, files=files
            )
            result = resp.json()

            if not result.get("ok"):
                logger.error(
                    "bale.%s api_error chat_id=%s response=%s",
                    method,
                    chat_id,
                    result,
                )

            return result

        except Exception as e:
            logger.error(
                "bale.%s failed chat_id=%s filename=%s error=%s",
                method,
                chat_id,
                filename,
                str(e),
                exc_info=True,
            )
            raise

    async def send_document(
        self,
        token: str,
        chat_id: str,
        filename: str,
        content: bytes,
        caption: Optional[str] = None,
        **params: Any,
    ) -> Dict[str, Any]:
        """Send document."""
        return await self._send_binary(
            token,
            method="sendDocument",
            file_field="document",
            chat_id=chat_id,
            filename=filename,
            content=content,
            caption=caption,
            **params,
        )

    async def send_photo(
        self,
        token: str,
        chat_id: str,
        filename: str,
        content: bytes,
        caption: Optional[str] = None,
        **params: Any,
    ) -> Dict[str, Any]:
        """Send photo."""
        return await self._send_binary(
            token,
            method="sendPhoto",
            file_field="photo",
            chat_id=chat_id,
            filename=filename,
            content=content,
            caption=caption,
            **params,
        )

    async def get_updates(
        self,
        token: str,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        timeout: Optional[int] = None,
        allowed_updates: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        """Get updates."""
        data: Dict[str, Any] = {}
        if offset is not None:
            data["offset"] = int(offset)
        if limit is not None:
            data["limit"] = int(limit)
        if timeout is not None:
            data["timeout"] = int(timeout)
        if allowed_updates is not None:
            data["allowed_updates"] = json.dumps(
                list(allowed_updates), ensure_ascii=False
            )

        logger.debug(
            "bale.getUpdates target=%s offset=%s timeout=%s",
            self._safe_api_target(token, "getUpdates"),
            offset,
            timeout,
        )

        try:
            resp = await self._client.post(
                self._api_url(token, "getUpdates"), data=data
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            logger.error(
                "bale.getUpdates failed target=%s error=%s",
                self._safe_api_target(token, "getUpdates"),
                str(e),
                exc_info=True,
            )
            return {"ok": False, "description": str(e)}

        # Log received updates to aid polling diagnostics.
        if payload.get("ok") and payload.get("result"):
            for update in payload["result"]:
                logger.debug(
                    "bale.update.received %s",
                    json.dumps(update, ensure_ascii=False),
                )

        elif not payload.get("ok"):
            logger.error(
                "bale.getUpdates api_error=%s",
                payload,
            )

        return payload

    async def get_file(self, token: str, file_id: str) -> Dict[str, Any]:
        """Get file."""
        data: Dict[str, Any] = {"file_id": str(file_id)}
        logger.debug(
            "bale.getFile target=%s file_id=%s",
            self._safe_api_target(token, "getFile"),
            file_id,
        )
        resp = await self._client.post(self._api_url(token, "getFile"), data=data)
        payload: Any
        try:
            payload = resp.json()
        except Exception:
            payload = {
                "ok": False,
                "error_code": resp.status_code,
                "description": truncate_text(resp.text, 500),
            }
        return (
            payload
            if isinstance(payload, dict)
            else {"ok": False, "description": "invalid json payload"}
        )

    async def download_file(
        self, token: str, file_path: str
    ) -> tuple[bytes, Optional[str]]:
        """Download file."""
        url = self.file_url(token, file_path)
        logger.debug("bale.downloadFile url=%s", url)
        resp = await self._client.get(url)
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type")

    async def close(self) -> None:
        """Close."""
        await self._client.aclose()

