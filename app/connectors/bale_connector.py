"""
Module Overview
---------------
Purpose: Platform connector implementations and connector registry abstractions.
Documentation Standard: module/class/public-method docstrings.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings
from app.utils.logging_utils import redact_secret, truncate_text
from app.utils.proxy_utils import build_proxy_url, redact_proxy_url


class BaleConnector:
    """Represents bale connector."""

    async def send_text(
        self,
        instance: str,
        chat_id: str,
        text: str,
        quoted: Optional[Dict] = None,
        reply_markup: Any = None,
    ) -> Dict:
        """Send text."""
        raise NotImplementedError

    async def send_media(
        self,
        instance: str,
        chat_id: str,
        media_url_or_bytes: Any,
        filename: str,
        caption: Optional[str] = None,
        quoted: Optional[Dict] = None,
        reply_markup: Any = None,
    ) -> Dict:
        """Send media."""
        raise NotImplementedError

    async def connect(
        self,
        instance: str,
        params: Dict[str, Any],
        proxy: Optional[dict[str, Any]] = None,
    ) -> None:
        """Connect."""
        return None

    async def disconnect(self, instance: str) -> None:
        """Disconnect."""
        return None

    async def get_updates(
        self, instance: str, offset: Optional[int] = None, timeout: Optional[int] = None
    ) -> Dict:
        """Get updates."""
        raise NotImplementedError

    async def download_file_by_id(
        self, instance: str, file_id: str
    ) -> Tuple[bytes, Optional[str], Optional[str]]:
        """Download file by id."""
        raise NotImplementedError

    async def mark_as_read(self, instance: str, message_ids: List[str]) -> None:
        """Mark as read."""
        return None

    async def close(self) -> None:
        """Close."""
        return None


@dataclass(frozen=True)
class BaleInstanceConfig:
    """Configuration model for bale instance."""

    token: str
    api_base_url: str
    file_base_url: str
    proxy_url: Optional[str]


@dataclass
class BaleInstanceRuntime:
    """Represents bale instance runtime."""

    instance_key: str
    cfg: BaleInstanceConfig
    client: httpx.AsyncClient
    file_client: httpx.AsyncClient


class BaleBotConnector(BaleConnector):
    """Represents bale bot connector."""

    def __init__(self):
        """Initialize the instance."""
        self._instances: Dict[str, BaleInstanceRuntime] = {}
        self._logger = logging.getLogger("app.connectors.bale")

    def _get_runtime(self, instance: str) -> BaleInstanceRuntime:
        """Internal helper to get runtime."""
        runtime = self._instances.get(instance)
        if not runtime:
            raise RuntimeError(f"Bale instance '{instance}' is not configured")
        return runtime

    @staticmethod
    def _normalize_api_base_url(value: str) -> str:
        """Internal helper to normalize api base url."""
        return str(value or "").strip().rstrip("/")

    @staticmethod
    def _normalize_file_base_url(value: str) -> str:
        """Internal helper to normalize file base url."""
        return str(value or "").strip().rstrip("/")

    @staticmethod
    def _api_url(cfg: BaleInstanceConfig, method: str) -> str:
        """Internal helper to api url."""
        return (
            f"{cfg.api_base_url}/bot{cfg.token}/{str(method or '').strip().lstrip('/')}"
        )

    @staticmethod
    def _file_url(cfg: BaleInstanceConfig, file_path: str) -> str:
        """Internal helper to file url."""
        return f"{cfg.file_base_url}/bot{cfg.token}/{str(file_path or '').lstrip('/')}"

    @staticmethod
    def _safe_token(token: str) -> str:
        """Internal helper to redact tokens in logs."""
        return redact_secret(token) if settings.LOG_REDACT_SECRETS else token

    def _safe_api_target(self, cfg: BaleInstanceConfig, method: str) -> str:
        """Internal helper to build a redacted API target for logs."""
        target = self._api_url(cfg, method)
        return target.replace(cfg.token, self._safe_token(cfg.token))

    @staticmethod
    def _error_code(exc: Exception) -> str:
        """Internal helper to classify connector transport failures."""
        if isinstance(exc, httpx.ConnectError):
            return "connect_error"
        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
        if isinstance(exc, httpx.HTTPStatusError):
            return "http_status_error"
        if isinstance(exc, httpx.RequestError):
            return "request_error"
        return "unexpected_error"

    async def _request(
        self,
        runtime: BaleInstanceRuntime,
        method: str,
        payload: Optional[dict[str, Any]] = None,
        files: Optional[dict[str, Any]] = None,
        log_error: bool = True,
    ) -> dict[str, Any]:
        """Internal helper to request."""
        target = self._safe_api_target(runtime.cfg, method)
        payload_preview = truncate_text(str(payload), settings.LOG_PAYLOAD_TRUNCATE)

        try:
            response = await runtime.client.post(
                self._api_url(runtime.cfg, method),
                data=payload or {},
                files=files,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError("Invalid Bale response payload")
            return data
        except httpx.ConnectError as exc:
            if log_error:
                self._logger.warning(
                    "bale.request connect_error instance=%s method=%s target=%s proxy=%s payload=%s error=%s",
                    runtime.instance_key,
                    method,
                    target,
                    redact_proxy_url(runtime.cfg.proxy_url),
                    payload_preview,
                    str(exc),
                )
            raise
        except httpx.TimeoutException as exc:
            if log_error:
                self._logger.warning(
                    "bale.request timeout instance=%s method=%s target=%s proxy=%s payload=%s error=%s",
                    runtime.instance_key,
                    method,
                    target,
                    redact_proxy_url(runtime.cfg.proxy_url),
                    payload_preview,
                    str(exc),
                )
            raise
        except httpx.HTTPStatusError as exc:
            if log_error:
                response = exc.response
                body = ""
                try:
                    body = (
                        truncate_text(
                            response.text or "", settings.LOG_PAYLOAD_TRUNCATE
                        )
                        if response is not None
                        else ""
                    )
                except Exception:
                    body = ""
                self._logger.error(
                    "bale.request http_error instance=%s method=%s target=%s status=%s body=%s error=%s",
                    runtime.instance_key,
                    method,
                    target,
                    response.status_code if response is not None else "unknown",
                    body,
                    str(exc),
                    exc_info=True,
                )
            raise
        except httpx.RequestError as exc:
            if log_error:
                self._logger.error(
                    "bale.request request_error instance=%s method=%s target=%s proxy=%s payload=%s error=%s",
                    runtime.instance_key,
                    method,
                    target,
                    redact_proxy_url(runtime.cfg.proxy_url),
                    payload_preview,
                    str(exc),
                    exc_info=True,
                )
            raise
        except Exception as exc:
            if log_error:
                self._logger.error(
                    "bale.request unexpected_error instance=%s method=%s target=%s payload=%s error=%s",
                    runtime.instance_key,
                    method,
                    target,
                    payload_preview,
                    str(exc),
                    exc_info=True,
                )
            raise

    async def _create_runtime(
        self, instance: str, cfg: BaleInstanceConfig
    ) -> BaleInstanceRuntime:
        """Internal helper to create runtime."""
        client = httpx.AsyncClient(timeout=30, proxy=cfg.proxy_url)
        file_client = httpx.AsyncClient(timeout=30, proxy=cfg.proxy_url)
        runtime = BaleInstanceRuntime(
            instance_key=str(instance), cfg=cfg, client=client, file_client=file_client
        )
        probe = await self._request(runtime, "getMe")
        if not probe.get("ok"):
            await runtime.client.aclose()
            await runtime.file_client.aclose()
            raise RuntimeError(
                str(probe.get("description") or "Bale credentials validation failed")
            )
        return runtime

    async def connect(
        self,
        instance: str,
        params: Dict[str, Any],
        proxy: Optional[dict[str, Any]] = None,
    ) -> None:
        """Connect."""
        token = (params.get("bale_token") or "").strip()
        api_base_url = self._normalize_api_base_url(
            params.get("bale_api_base_url") or settings.BALE_API_BASE_URL
        )
        file_base_url = self._normalize_file_base_url(
            params.get("bale_file_base_url") or settings.BALE_FILE_BASE_URL
        )
        if not token:
            raise RuntimeError(
                f"Bale instance '{instance}' is not configured (missing bale_token)"
            )

        proxy_url = build_proxy_url(proxy)
        cfg = BaleInstanceConfig(
            token=token,
            api_base_url=api_base_url,
            file_base_url=file_base_url,
            proxy_url=proxy_url,
        )
        existing = self._instances.get(instance)
        if existing and existing.cfg == cfg:
            return

        if existing:
            try:
                await existing.client.aclose()
            except Exception:
                pass
            try:
                await existing.file_client.aclose()
            except Exception:
                pass

        self._instances[instance] = await self._create_runtime(instance, cfg)
        safe_token = redact_secret(token) if settings.LOG_REDACT_SECRETS else token
        self._logger.info(
            "configured instance=%s api_base_url=%s file_base_url=%s proxy=%s token=%s",
            instance,
            api_base_url,
            file_base_url,
            redact_proxy_url(proxy_url),
            safe_token,
        )

    async def disconnect(self, instance: str) -> None:
        """Disconnect."""
        runtime = self._instances.pop(instance, None)
        if not runtime:
            return
        await runtime.client.aclose()
        await runtime.file_client.aclose()

    @staticmethod
    def _is_image(filename: str, content_type: Optional[str]) -> bool:
        """Internal helper to is image."""
        ctype = str(content_type or "").strip().lower()
        if ctype.startswith("image/"):
            return True
        return (
            str(filename or "")
            .lower()
            .endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
        )

    @staticmethod
    def _is_video(filename: str, content_type: Optional[str]) -> bool:
        """Internal helper to is video."""
        ctype = str(content_type or "").strip().lower()
        if ctype.startswith("video/"):
            return True
        return str(filename or "").lower().endswith((".mp4", ".webm", ".mov", ".m4v"))

    @staticmethod
    def _is_audio(filename: str, content_type: Optional[str]) -> bool:
        """Internal helper to is audio."""
        ctype = str(content_type or "").strip().lower()
        if ctype.startswith("audio/"):
            return True
        return (
            str(filename or "")
            .lower()
            .endswith((".mp3", ".ogg", ".wav", ".m4a", ".aac"))
        )

    def _build_share_phone_components(self) -> Optional[dict[str, Any]]:
        """Internal helper to build share phone components."""
        if not settings.BALE_SHARE_PHONE_BUTTON:
            return None

        text = (
            str(settings.BALE_SHARE_PHONE_BUTTON_TEXT or "").strip()
            or "Share phone number"
        )
        return {"keyboard": [[{"text": text, "request_contact": True}]]}

    async def send_text(
        self,
        instance: str,
        chat_id: str,
        text: str,
        quoted: Optional[Dict] = None,
        reply_markup: Any = None,
    ) -> Dict:
        """Send text."""
        runtime = self._get_runtime(instance)
        components = self._resolve_reply_markup(reply_markup)
        payload: dict[str, Any] = {"chat_id": str(chat_id), "text": text}
        if components:
            import json

            payload["reply_markup"] = json.dumps(components, ensure_ascii=False)
        if isinstance(quoted, dict) and quoted.get("id") is not None:
            payload["reply_to_message_id"] = str(quoted.get("id"))

        try:
            if settings.LOG_MESSAGE_CONTENT:
                self._logger.info(
                    "send_text instance=%s chat_id=%s text=%s reply_markup=%s",
                    instance,
                    chat_id,
                    truncate_text(text, settings.LOG_PAYLOAD_TRUNCATE),
                    self._reply_markup_summary(components),
                )
            else:
                self._logger.info(
                    "send_text instance=%s chat_id=%s text_len=%s text_is_empty=%s reply_markup=%s",
                    instance,
                    chat_id,
                    len(text or ""),
                    not bool(text or "").strip(),
                    self._reply_markup_summary(components),
                )
            result = await self._request(
                runtime, "sendMessage", payload=payload, log_error=False
            )
            message = (
                result.get("result") if isinstance(result.get("result"), dict) else {}
            )
            mid = message.get("message_id")
            return {
                "id": str(mid) if mid is not None else None,
                "raw": message or result.get("result"),
            }
        except Exception as exc:
            self._logger.error(
                "send_text failed instance=%s chat_id=%s text_len=%s text_is_empty=%s error_type=%s error=%s",
                instance,
                chat_id,
                len(text or ""),
                not bool(text or "").strip(),
                type(exc).__name__,
                str(exc),
                exc_info=True,
            )
            raise RuntimeError(str(exc)) from exc

    async def _resolve_media(
        self, runtime: BaleInstanceRuntime, media_url_or_bytes: Any
    ) -> tuple[bytes, Optional[str]]:
        """Internal helper to resolve media."""
        if isinstance(media_url_or_bytes, (bytes, bytearray)):
            return bytes(media_url_or_bytes), None

        if isinstance(media_url_or_bytes, str):
            raw = media_url_or_bytes.strip()
            if raw.startswith("data:"):
                m = re.match(
                    r"^data:(?P<type>[^;]+);base64,(?P<data>.+)$", raw, flags=re.DOTALL
                )
                if not m:
                    raise ValueError("Invalid data URL")
                return base64.b64decode(m.group("data")), m.group("type")

            if raw.startswith("http://") or raw.startswith("https://"):
                resp = await runtime.file_client.get(raw, follow_redirects=True)
                resp.raise_for_status()
                return resp.content, resp.headers.get("content-type")

        raise ValueError(
            "Unsupported media type; expected bytes, data: URL, or http(s) URL"
        )

    async def send_media(
        self,
        instance: str,
        chat_id: str,
        media_url_or_bytes: Any,
        filename: str,
        caption: Optional[str] = None,
        quoted: Optional[Dict] = None,
        reply_markup: Any = None,
    ) -> Dict:
        """Send media."""
        runtime = self._get_runtime(instance)
        content, content_type = await self._resolve_media(runtime, media_url_or_bytes)

        payload: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption is not None:
            payload["caption"] = caption
        if isinstance(quoted, dict) and quoted.get("id") is not None:
            payload["reply_to_message_id"] = str(quoted.get("id"))
        components = self._resolve_reply_markup(reply_markup)
        if components:
            import json

            payload["reply_markup"] = json.dumps(components, ensure_ascii=False)

        if self._is_image(filename, content_type):
            method = "sendPhoto"
            field = "photo"
        elif self._is_video(filename, content_type):
            method = "sendVideo"
            field = "video"
        elif self._is_audio(filename, content_type):
            method = "sendAudio"
            field = "audio"
        else:
            method = "sendDocument"
            field = "document"

        files = {
            field: (
                filename,
                content,
                content_type or "application/octet-stream",
            )
        }

        try:
            if settings.LOG_MESSAGE_CONTENT:
                self._logger.info(
                    "send_media instance=%s chat_id=%s filename=%s caption=%s reply_markup=%s",
                    instance,
                    chat_id,
                    filename,
                    truncate_text(caption or "", settings.LOG_PAYLOAD_TRUNCATE),
                    self._reply_markup_summary(components),
                )
            else:
                self._logger.info(
                    "send_media instance=%s chat_id=%s filename=%s caption_len=%s reply_markup=%s",
                    instance,
                    chat_id,
                    filename,
                    len(caption or ""),
                    self._reply_markup_summary(components),
                )
            result = await self._request(
                runtime, method, payload=payload, files=files, log_error=False
            )
            message = (
                result.get("result") if isinstance(result.get("result"), dict) else {}
            )
            mid = message.get("message_id")
            return {
                "id": str(mid) if mid is not None else None,
                "raw": message or result.get("result"),
                "content_type": content_type,
            }
        except Exception as exc:
            self._logger.error(
                "send_media failed instance=%s chat_id=%s filename=%s error=%s",
                instance,
                chat_id,
                filename,
                str(exc),
                exc_info=True,
            )
            raise RuntimeError(str(exc)) from exc

    async def get_updates(
        self, instance: str, offset: Optional[int] = None, timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """Get updates."""
        runtime = self._get_runtime(instance)
        payload: dict[str, Any] = {}
        if offset is not None:
            payload["offset"] = int(offset)
        if timeout is not None:
            payload["timeout"] = int(timeout)
        try:
            response = await self._request(
                runtime, "getUpdates", payload=payload, log_error=False
            )
            return {
                "ok": bool(response.get("ok")),
                "result": response.get("result") or [],
                "description": response.get("description"),
                "error_code": response.get("error_code"),
            }
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as exc:
            error_code = self._error_code(exc)
            self._logger.warning(
                "bale.getUpdates transport_error instance=%s offset=%s timeout=%s target=%s proxy=%s error_code=%s error=%s",
                instance,
                offset,
                timeout,
                self._safe_api_target(runtime.cfg, "getUpdates"),
                redact_proxy_url(runtime.cfg.proxy_url),
                error_code,
                str(exc),
            )
            return {"ok": False, "description": str(exc), "error_code": error_code}
        except Exception as exc:
            self._logger.error(
                "bale.getUpdates failed instance=%s offset=%s timeout=%s error=%s",
                instance,
                offset,
                timeout,
                str(exc),
                exc_info=True,
            )
            return {"ok": False, "description": str(exc)}

    async def download_file_by_id(
        self, instance: str, file_id: str
    ) -> Tuple[bytes, Optional[str], Optional[str]]:
        """Download file by id."""
        runtime = self._get_runtime(instance)
        try:
            info = await self._request(
                runtime, "getFile", payload={"file_id": str(file_id)}, log_error=False
            )
            result = info.get("result") if isinstance(info.get("result"), dict) else {}
            file_path = str(
                result.get("file_path") or result.get("file_id") or file_id
            ).strip()
            if not file_path:
                return b"", None, None

            response = await runtime.file_client.get(
                self._file_url(runtime.cfg, file_path)
            )
            response.raise_for_status()
            return (
                bytes(response.content),
                response.headers.get("content-type"),
                file_path,
            )
        except Exception as exc:
            self._logger.error(
                "download_file_by_id failed instance=%s file_id=%s error=%s",
                instance,
                file_id,
                str(exc),
                exc_info=True,
            )
            return b"", None, None

    async def close(self) -> None:
        """Close."""
        for runtime in list(self._instances.values()):
            try:
                await runtime.client.aclose()
            except Exception:
                pass
            try:
                await runtime.file_client.aclose()
            except Exception:
                pass
        self._instances.clear()

    def _resolve_reply_markup(self, reply_markup: Any) -> Optional[dict[str, Any]]:
        """Resolve explicit or default reply markup for outbound messages."""
        if reply_markup is False:
            return None
        if isinstance(reply_markup, dict):
            return reply_markup
        return self._build_share_phone_components()

    @staticmethod
    def _reply_markup_summary(reply_markup: Optional[dict[str, Any]]) -> str:
        """Summarize a Bale reply markup payload for runtime debugging."""
        if not isinstance(reply_markup, dict):
            return "none"
        if reply_markup.get("remove_keyboard"):
            return "remove_keyboard"
        keyboard = (
            reply_markup.get("keyboard")
            if isinstance(reply_markup.get("keyboard"), list)
            else []
        )
        rows = len(keyboard)
        buttons = 0
        items: list[list[str]] = []
        for row in keyboard:
            if not isinstance(row, list):
                continue
            row_labels: list[str] = []
            buttons += len(row)
            for item in row:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or "").strip()
                row_labels.append(text)
            items.append(row_labels)
        flags = []
        if reply_markup.get("one_time_keyboard"):
            flags.append("one_time")
        if reply_markup.get("resize_keyboard"):
            flags.append("resize")
        suffix = f" flags={','.join(flags)}" if flags else ""
        return f"rows={rows} buttons={buttons} items={items}{suffix}"


bale = BaleBotConnector()
