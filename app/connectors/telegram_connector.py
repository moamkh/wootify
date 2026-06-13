"""
Module Overview
---------------
Purpose: Platform connector implementations and connector registry abstractions.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import logging
import mimetypes
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from telegram import Bot, BotCommand, InputFile, KeyboardButton, ReplyKeyboardMarkup
from telegram.request import HTTPXRequest

from app.config import settings
from app.utils.logging_utils import redact_secret, truncate_text
from app.utils.proxy_utils import build_proxy_url, redact_proxy_url


@dataclass(frozen=True)
class TelegramInstanceConfig:
    """Configuration model for telegram instance."""
    token: str
    api_base_url: str
    file_base_url: str
    proxy_url: Optional[str]


@dataclass
class TelegramInstanceRuntime:
    """Represents telegram instance runtime."""
    cfg: TelegramInstanceConfig
    bot: Bot
    file_client: httpx.AsyncClient


class TelegramBotConnector:
    """Represents telegram bot connector."""
    def __init__(self) -> None:
        """Initialize the instance."""
        self._instances: dict[str, TelegramInstanceRuntime] = {}
        self._logger = logging.getLogger('app.connectors.telegram')

    @staticmethod
    def _normalize_api_base_url(value: str) -> str:
        """Internal helper to normalize api base url."""
        raw = str(value or '').strip().rstrip('/')
        if not raw:
            raw = str(settings.TELEGRAM_API_BASE_URL).strip().rstrip('/')
        if not raw.lower().endswith('/bot'):
            raw = f'{raw}/bot'
        return raw

    @staticmethod
    def _normalize_file_base_url(value: str) -> str:
        """Internal helper to normalize file base url."""
        raw = str(value or '').strip().rstrip('/')
        if not raw:
            raw = str(settings.TELEGRAM_FILE_BASE_URL).strip().rstrip('/')
        if raw.lower().endswith('/file/bot'):
            return raw
        if raw.lower().endswith('/file'):
            return f'{raw}/bot'
        if raw.lower().endswith('/bot'):
            root = raw[:-4].rstrip('/')
            if root.lower().endswith('/file'):
                return f'{root}/bot'
            return f'{root}/file/bot'
        return f'{raw}/file/bot'

    @staticmethod
    def _is_image(filename: str, content_type: Optional[str]) -> bool:
        """Internal helper to is image."""
        ctype = str(content_type or '').strip().lower()
        if ctype.startswith('image/'):
            return True
        return str(filename or '').lower().endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif'))

    @staticmethod
    def _is_video(filename: str, content_type: Optional[str]) -> bool:
        """Internal helper to is video."""
        ctype = str(content_type or '').strip().lower()
        if ctype.startswith('video/'):
            return True
        return str(filename or '').lower().endswith(('.mp4', '.webm', '.mov', '.m4v'))

    @staticmethod
    def _is_audio(filename: str, content_type: Optional[str]) -> bool:
        """Internal helper to is audio."""
        ctype = str(content_type or '').strip().lower()
        if ctype.startswith('audio/'):
            return True
        return str(filename or '').lower().endswith(('.mp3', '.ogg', '.wav', '.m4a', '.aac'))

    @staticmethod
    def _to_chat_target(chat_id: str) -> int | str:
        """Internal helper to to chat target."""
        cid = str(chat_id or '').strip()
        if re.fullmatch(r'-?\d+', cid):
            return int(cid)
        return cid

    @staticmethod
    def _to_reply_message_id(quoted: Optional[dict[str, Any]]) -> Optional[int]:
        """Internal helper to to reply message id."""
        if not isinstance(quoted, dict):
            return None
        value = str(quoted.get('id') or '').strip()
        if not value or not re.fullmatch(r'-?\d+', value):
            return None
        return int(value)

    @staticmethod
    def _is_private_chat(chat_id: str) -> bool:
        """Internal helper to is private chat."""
        cid = str(chat_id or '').strip()
        if re.fullmatch(r'-?\d+', cid):
            return int(cid) > 0
        if cid.startswith('@'):
            return False
        return False

    def _build_share_phone_markup(self, chat_id: str) -> Optional[ReplyKeyboardMarkup]:
        """Internal helper to build share phone markup."""
        if not settings.TELEGRAM_SHARE_PHONE_BUTTON:
            return None
        if not self._is_private_chat(chat_id):
            return None
        text = str(settings.TELEGRAM_SHARE_PHONE_BUTTON_TEXT or '').strip() or 'Share phone number'
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=text, request_contact=True)]],
            resize_keyboard=True,
        )

    async def _register_commands(self, bot: Bot) -> None:
        """Internal helper to register commands."""
        try:
            await bot.set_my_commands(
                commands=[
                    BotCommand(command='start', description='Start'),
                    BotCommand(command='share_phone', description='Share phone number'),
                    BotCommand(command='help', description='Help'),
                ]
            )
        except Exception as exc:
            self._logger.warning('failed to set telegram bot commands error=%s', str(exc))

    async def _create_runtime(self, cfg: TelegramInstanceConfig) -> TelegramInstanceRuntime:
        """Internal helper to create runtime."""
        proxy = cfg.proxy_url
        # Regular requests: standard timeouts
        request = HTTPXRequest(
            proxy=proxy,
            read_timeout=30.0,
            write_timeout=10.0,
            connect_timeout=5.0,
            pool_timeout=1.0,
        )
        # getUpdates long-polling needs a read timeout longer than the poll timeout
        get_updates_request = HTTPXRequest(
            proxy=proxy,
            read_timeout=35.0,
            write_timeout=10.0,
            connect_timeout=5.0,
            pool_timeout=1.0,
        )
        bot = Bot(
            token=cfg.token,
            base_url=cfg.api_base_url,
            base_file_url=cfg.file_base_url,
            request=request,
            get_updates_request=get_updates_request,
        )
        file_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=1.0),
            proxy=proxy,
        )
        try:
            await bot.initialize()
            await bot.get_me()
            await self._register_commands(bot)
        except Exception:
            await self._shutdown_runtime(
                TelegramInstanceRuntime(cfg=cfg, bot=bot, file_client=file_client)
            )
            raise
        return TelegramInstanceRuntime(cfg=cfg, bot=bot, file_client=file_client)

    async def _shutdown_runtime(self, runtime: TelegramInstanceRuntime) -> None:
        """Internal helper to shutdown runtime."""
        try:
            await runtime.bot.shutdown()
        except Exception:
            pass
        try:
            await runtime.file_client.aclose()
        except Exception:
            pass

    def _get_runtime(self, instance: str) -> TelegramInstanceRuntime:
        """Internal helper to get runtime."""
        runtime = self._instances.get(instance)
        if not runtime:
            raise RuntimeError(f"Telegram instance '{instance}' is not configured")
        return runtime

    async def connect(
        self,
        instance: str,
        params: dict[str, Any],
        proxy: Optional[dict[str, Any]] = None,
    ) -> None:
        """Connect."""
        token = str(params.get('telegram_token') or '').strip()
        api_base_url = self._normalize_api_base_url(params.get('telegram_api_base_url') or settings.TELEGRAM_API_BASE_URL)
        file_base_url = self._normalize_file_base_url(
            params.get('telegram_file_base_url') or settings.TELEGRAM_FILE_BASE_URL
        )
        if not token:
            raise RuntimeError(f"Telegram instance '{instance}' is not configured (missing telegram_token)")

        proxy_url = build_proxy_url(proxy)
        cfg = TelegramInstanceConfig(
            token=token,
            api_base_url=api_base_url,
            file_base_url=file_base_url,
            proxy_url=proxy_url,
        )
        existing = self._instances.get(instance)
        if existing and existing.cfg == cfg:
            return

        if existing:
            await self._shutdown_runtime(existing)

        runtime = await self._create_runtime(cfg)
        self._instances[instance] = runtime
        safe_token = redact_secret(token) if settings.LOG_REDACT_SECRETS else token
        self._logger.info(
            'configured instance=%s api_base_url=%s file_base_url=%s proxy=%s token=%s',
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
        await self._shutdown_runtime(runtime)

    async def send_text(
        self,
        instance: str,
        chat_id: str,
        text: str,
        quoted: Optional[dict[str, Any]] = None,
        reply_markup: Any = None,
    ) -> dict[str, Any]:
        """Send text."""
        runtime = self._get_runtime(instance)
        reply_to = self._to_reply_message_id(quoted)
        markup = self._resolve_reply_markup(chat_id, reply_markup)
        kwargs: dict[str, Any] = {}
        if reply_to is not None:
            kwargs['reply_to_message_id'] = reply_to
        if markup is not None:
            kwargs['reply_markup'] = markup

        try:
            if settings.LOG_MESSAGE_CONTENT:
                self._logger.info(
                    'send_text instance=%s chat_id=%s text=%s',
                    instance,
                    chat_id,
                    truncate_text(text, settings.LOG_PAYLOAD_TRUNCATE),
                )
            else:
                self._logger.info('send_text instance=%s chat_id=%s text_len=%s', instance, chat_id, len(text or ''))
            message = await runtime.bot.send_message(chat_id=self._to_chat_target(chat_id), text=text, **kwargs)
            return {
                'id': str(message.message_id) if getattr(message, 'message_id', None) is not None else None,
                'raw': message.to_dict() if hasattr(message, 'to_dict') else None,
            }
        except Exception as exc:
            error_msg = str(exc) or f"{type(exc).__name__}: connector send_text failed"
            self._logger.error(
                'send_text failed instance=%s chat_id=%s error_type=%s error=%s',
                instance,
                chat_id,
                type(exc).__name__,
                error_msg,
                exc_info=True,
            )
            raise RuntimeError(error_msg) from exc

    async def _resolve_media(
        self,
        runtime: TelegramInstanceRuntime,
        media_url_or_bytes: Any,
    ) -> tuple[bytes, Optional[str]]:
        """Internal helper to resolve media."""
        if isinstance(media_url_or_bytes, (bytes, bytearray)):
            return bytes(media_url_or_bytes), None

        if isinstance(media_url_or_bytes, str):
            raw = media_url_or_bytes.strip()
            if raw.startswith('data:'):
                import base64

                m = re.match(r'^data:(?P<type>[^;]+);base64,(?P<data>.+)$', raw, flags=re.DOTALL)
                if not m:
                    raise ValueError('Invalid data URL')
                return base64.b64decode(m.group('data')), m.group('type')

            if raw.startswith('http://') or raw.startswith('https://'):
                resp = await runtime.file_client.get(raw, follow_redirects=True)
                resp.raise_for_status()
                return resp.content, resp.headers.get('content-type')

        raise ValueError('Unsupported media type; expected bytes, data: URL, or http(s) URL')

    async def send_media(
        self,
        instance: str,
        chat_id: str,
        media_url_or_bytes: Any,
        filename: str,
        caption: Optional[str] = None,
        quoted: Optional[dict[str, Any]] = None,
        reply_markup: Any = None,
    ) -> dict[str, Any]:
        """Send media."""
        runtime = self._get_runtime(instance)
        reply_to = self._to_reply_message_id(quoted)
        markup = self._resolve_reply_markup(chat_id, reply_markup)
        kwargs: dict[str, Any] = {}
        if reply_to is not None:
            kwargs['reply_to_message_id'] = reply_to
        if markup is not None:
            kwargs['reply_markup'] = markup

        content, content_type = await self._resolve_media(runtime, media_url_or_bytes)
        payload = InputFile(content, filename=filename)

        try:
            if self._is_image(filename, content_type):
                message = await runtime.bot.send_photo(
                    chat_id=self._to_chat_target(chat_id),
                    photo=payload,
                    caption=caption,
                    **kwargs,
                )
            elif self._is_video(filename, content_type):
                message = await runtime.bot.send_video(
                    chat_id=self._to_chat_target(chat_id),
                    video=payload,
                    caption=caption,
                    **kwargs,
                )
            elif self._is_audio(filename, content_type):
                message = await runtime.bot.send_audio(
                    chat_id=self._to_chat_target(chat_id),
                    audio=payload,
                    caption=caption,
                    **kwargs,
                )
            else:
                message = await runtime.bot.send_document(
                    chat_id=self._to_chat_target(chat_id),
                    document=payload,
                    caption=caption,
                    **kwargs,
                )
            return {
                'id': str(message.message_id) if getattr(message, 'message_id', None) is not None else None,
                'raw': message.to_dict() if hasattr(message, 'to_dict') else None,
                'content_type': content_type,
            }
        except Exception as exc:
            error_msg = str(exc) or f"{type(exc).__name__}: connector send_media failed"
            self._logger.error(
                'send_media failed instance=%s chat_id=%s filename=%s error_type=%s error=%s',
                instance,
                chat_id,
                filename,
                type(exc).__name__,
                error_msg,
                exc_info=True,
            )
            raise RuntimeError(error_msg) from exc

    async def update_message(
        self,
        instance: str,
        chat_id: str,
        message_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Edit an existing message."""
        raise NotImplementedError

    async def delete_message(
        self,
        instance: str,
        chat_id: str,
        message_id: str,
    ) -> dict[str, Any]:
        """Delete a message."""
        raise NotImplementedError

    async def get_updates(
        self,
        instance: str,
        offset: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> dict[str, Any]:
        """Get updates."""
        runtime = self._get_runtime(instance)
        try:
            updates = await runtime.bot.get_updates(
                offset=int(offset) if offset is not None else None,
                timeout=int(timeout) if timeout is not None else None,
            )
            rows: list[dict[str, Any]] = []
            for item in updates or []:
                if hasattr(item, 'to_dict'):
                    rows.append(item.to_dict())
                elif isinstance(item, dict):
                    rows.append(item)
            return {'ok': True, 'result': rows}
        except Exception as exc:
            self._logger.error(
                'telegram.getUpdates failed instance=%s offset=%s timeout=%s error=%s',
                instance,
                offset,
                timeout,
                str(exc),
                exc_info=True,
            )
            return {'ok': False, 'description': str(exc)}

    async def download_file_by_id(self, instance: str, file_id: str) -> tuple[bytes, Optional[str], Optional[str]]:
        """Download file by id."""
        runtime = self._get_runtime(instance)
        try:
            telegram_file = await runtime.bot.get_file(str(file_id))
            file_path = str(getattr(telegram_file, 'file_path', '') or '')
            content_type = mimetypes.guess_type(file_path)[0]

            if hasattr(telegram_file, 'download_as_bytearray'):
                data = await telegram_file.download_as_bytearray()
                return bytes(data or b''), content_type, file_path or str(file_id)

            if file_path:
                if re.match(r'^https?://', file_path, flags=re.IGNORECASE):
                    download_url = file_path
                else:
                    download_url = (
                        f'{runtime.cfg.file_base_url.rstrip("/")}/{runtime.cfg.token}/{file_path.lstrip("/")}'
                    )
                response = await runtime.file_client.get(download_url)
                response.raise_for_status()
                return bytes(response.content), content_type, file_path or str(file_id)

            return b'', content_type, file_path or str(file_id)
        except Exception as exc:
            self._logger.error(
                'download_file_by_id failed instance=%s file_id=%s error=%s',
                instance,
                file_id,
                str(exc),
                exc_info=True,
            )
            return b'', None, None

    async def close(self) -> None:
        """Close."""
        for runtime in list(self._instances.values()):
            await self._shutdown_runtime(runtime)
        self._instances.clear()

    def _resolve_reply_markup(self, chat_id: str, reply_markup: Any) -> Any:
        """Resolve explicit or default reply markup for outbound messages."""
        if reply_markup is False:
            return None
        if reply_markup is not None:
            return reply_markup
        return self._build_share_phone_markup(chat_id)


telegram = TelegramBotConnector()

