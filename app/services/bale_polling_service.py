"""
Module Overview
---------------
Purpose: Service-layer business logic for connector and synchronization workflows.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.connectors.registry import connector_registry
from app.db import SessionLocal
from app.services.bridge_service import BridgeService
from app.services.enterprise_bale_service import EnterpriseBaleService
from app.services.instance_service import InstanceService


class BalePollingService:
    """Service for bale polling domain workflows."""
    def __init__(self) -> None:
        """Initialize the instance."""
        self._logger = logging.getLogger('app.services.bale_polling')
        self._sms_logger = logging.getLogger('app.services.enterprise_sms')
        self._stop = asyncio.Event()
        self._manager_task: Optional[asyncio.Task] = None
        self._poll_tasks: dict[str, asyncio.Task] = {}
        self._last_update_ids: dict[str, str] = {}
        self._enterprise_sms_last_run: dict[str, float] = {}
        self._enterprise_sms_enabled_state: dict[str, bool] = {}
        self._share_phone_prompted: set[tuple[str, str]] = set()
        # Temporary debug dump for periodic enterprise SMS sync results.
        self._temp_sms_dump_path = (
            Path(__file__).resolve().parents[2] / 'data' / 'tmp-enterprise-smoke' / 'sms-sync-results.jsonl'
        )
        self._instances = InstanceService()
        self._bridge = BridgeService()
        self._enterprise = EnterpriseBaleService()

    async def start(self) -> None:
        """Start."""
        if self._manager_task and not self._manager_task.done():
            return
        self._stop.clear()
        self._manager_task = asyncio.create_task(self._run_manager())
        self._logger.info('started')

    async def stop(self) -> None:
        """Stop."""
        self._stop.set()
        for task in self._poll_tasks.values():
            task.cancel()
        self._poll_tasks.clear()
        self._last_update_ids.clear()
        self._enterprise_sms_last_run.clear()
        self._enterprise_sms_enabled_state.clear()
        self._share_phone_prompted.clear()
        if self._manager_task:
            self._manager_task.cancel()
            try:
                await self._manager_task
            except asyncio.CancelledError:
                pass
        await connector_registry.close_all()
        self._logger.info('stopped')

    async def _run_manager(self) -> None:
        """Internal helper to run manager."""
        while not self._stop.is_set():
            try:
                enabled = self._list_enabled_instance_keys()
                existing = set(self._poll_tasks.keys())

                for key in existing - enabled:
                    task = self._poll_tasks.pop(key)
                    task.cancel()
                    self._last_update_ids.pop(key, None)
                    self._logger.info('stopped polling instance=%s', key)

                for key in enabled - existing:
                    self._poll_tasks[key] = asyncio.create_task(self._run_instance(key))
                    self._logger.info('started polling instance=%s', key)
            except Exception as exc:
                self._logger.exception('manager loop failed: %s', exc)

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=10)
            except asyncio.TimeoutError:
                continue

    def _list_enabled_instance_keys(self) -> set[str]:
        """Internal helper to list enabled instance keys."""
        with SessionLocal() as db:
            runtimes = self._instances.list_runtime_enabled_instances(db)
            return {runtime.instance.instance_key for runtime in runtimes}

    async def _run_instance(self, instance_key: str) -> None:
        """Internal helper to run instance."""
        while not self._stop.is_set():
            poll_interval = settings.BALE_POLL_INTERVAL_SECONDS
            runtime_instance_id: Optional[str] = None
            try:
                with SessionLocal() as db:
                    runtime = self._instances.get_runtime_instance(db, instance_key)
                if not runtime or not runtime.instance.is_enabled:
                    await asyncio.sleep(5)
                    continue

                runtime_instance_id = runtime.instance.id
                platform_key = str(runtime.platform_type.key or '').strip().lower() or 'bale'
                connector = connector_registry.get(platform_key)
                cfg = runtime.platform_metadata
                poll_interval = self._poll_interval_seconds(platform_key, cfg)
                prompt_cfg = self._share_phone_prompt_config(platform_key, cfg)
                long_poll_timeout = self._long_poll_timeout_seconds(platform_key)

                await self._maybe_run_enterprise_sms_sync(
                    instance_key,
                    platform_key=platform_key,
                    platform_metadata=cfg,
                )

                await connector.connect(instance_key, cfg, runtime.proxy)

                offset = None
                last_update = self._merged_last_update_id(instance_key, runtime.runtime_state_last_update_id)
                if last_update and str(last_update).isdigit():
                    offset = int(last_update) + 1

                resp = await connector.get_updates(instance_key, offset=offset, timeout=long_poll_timeout)
                if not isinstance(resp, dict) or not resp.get('ok'):
                    await self._update_runtime_state_with_retry(
                        runtime_instance_id,
                        last_error=str((resp or {}).get('description') or f'{platform_key}_get_updates_failed'),
                        touch_sync=False,
                    )
                    await asyncio.sleep(5)
                    continue

                updates = resp.get('result') or []
                max_update = int(last_update) if str(last_update or '').isdigit() else None

                for update in updates:
                    if not isinstance(update, dict):
                        continue
                    update_id = update.get('update_id')
                    processed_update_id: Optional[str] = None
                    if update_id is not None and str(update_id).isdigit():
                        normalized_update_id = int(str(update_id))
                        max_update = max(max_update or normalized_update_id, normalized_update_id)
                        processed_update_id = str(normalized_update_id)

                    if platform_key == 'bale_enterprise':
                        try:
                            with SessionLocal() as db:
                                await self._enterprise.handle_platform_update(db, instance_key, update)
                        except Exception as exc:
                            self._logger.error(
                                'enterprise_update_error instance=%s update_id=%s error_type=%s error=%s',
                                instance_key,
                                processed_update_id,
                                type(exc).__name__,
                                str(exc),
                                exc_info=True,
                            )
                    else:
                        handled = await self._maybe_handle_local_command(
                            instance_key,
                            update,
                            platform_key=platform_key,
                            prompt_cfg=prompt_cfg,
                        )
                        if handled:
                            if processed_update_id:
                                self._remember_last_update_id(instance_key, processed_update_id)
                                await self._update_runtime_state_with_retry(
                                    runtime_instance_id,
                                    last_platform_update_id=processed_update_id,
                                    last_error=None,
                                    touch_sync=True,
                                )
                            continue

                        event = await self._platform_update_to_event(instance_key, platform_key, update, connector=connector)
                        if not event:
                            if processed_update_id:
                                self._remember_last_update_id(instance_key, processed_update_id)
                                await self._update_runtime_state_with_retry(
                                    runtime_instance_id,
                                    last_platform_update_id=processed_update_id,
                                    last_error=None,
                                    touch_sync=True,
                                )
                            continue

                        with SessionLocal() as db:
                            await self._bridge.ingest_platform_event(db, instance_key, event)

                    if processed_update_id:
                        self._remember_last_update_id(instance_key, processed_update_id)
                        await self._update_runtime_state_with_retry(
                            runtime_instance_id,
                            last_platform_update_id=processed_update_id,
                            last_error=None,
                            touch_sync=True,
                        )

                if max_update is not None:
                    self._remember_last_update_id(instance_key, str(max_update))
                await self._update_runtime_state_with_retry(
                    runtime_instance_id,
                    last_platform_update_id=str(max_update) if max_update is not None else None,
                    last_error=None,
                    touch_sync=True,
                )
            except asyncio.CancelledError:
                return
            except httpx.RequestError as exc:
                self._logger.warning(
                    'poll transport_error instance=%s error_type=%s error=%s',
                    instance_key,
                    type(exc).__name__,
                    str(exc),
                )
                if not runtime_instance_id:
                    with SessionLocal() as db:
                        runtime = self._instances.get_runtime_instance(db, instance_key)
                        runtime_instance_id = runtime.instance.id if runtime else None
                if runtime_instance_id:
                    await self._update_runtime_state_with_retry(
                        runtime_instance_id,
                        last_error=f'{type(exc).__name__}: {exc}',
                        touch_sync=False,
                    )
                await asyncio.sleep(5)
                continue
            except Exception as exc:
                self._logger.exception('poll error instance=%s error=%s', instance_key, exc)
                if not runtime_instance_id:
                    with SessionLocal() as db:
                        runtime = self._instances.get_runtime_instance(db, instance_key)
                        runtime_instance_id = runtime.instance.id if runtime else None
                if runtime_instance_id:
                    await self._update_runtime_state_with_retry(
                        runtime_instance_id,
                        last_error=str(exc),
                        touch_sync=False,
                    )
                await asyncio.sleep(5)
                continue

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=int(poll_interval))
            except asyncio.TimeoutError:
                continue

    async def _maybe_run_enterprise_sms_sync(
        self,
        instance_key: str,
        *,
        platform_key: str,
        platform_metadata: dict[str, Any],
    ) -> None:
        """Trigger enterprise SMS sync when the configured interval has elapsed."""
        if str(platform_key or '').strip().lower() != 'bale_enterprise':
            return

        enabled = self._enterprise.sms_sync_enabled(platform_metadata)
        previous_enabled = self._enterprise_sms_enabled_state.get(instance_key)
        self._enterprise_sms_enabled_state[instance_key] = enabled

        if previous_enabled is None or previous_enabled != enabled:
            self._sms_logger.info('sync.status instance=%s enabled=%s', instance_key, enabled)

        if not enabled:
            return

        interval_seconds = self._enterprise.sms_sync_interval_seconds(platform_metadata)
        now = asyncio.get_running_loop().time()
        last_run = float(self._enterprise_sms_last_run.get(instance_key, 0.0))
        if last_run and (now - last_run) < float(interval_seconds):
            return

        self._enterprise_sms_last_run[instance_key] = now
        with SessionLocal() as db:
            result = await self._enterprise.sync_external_sms_messages(db, instance_key)
        self._sms_logger.info(
            'sync.result instance=%s fetched=%s delivered=%s dropped=%s failed=%s last_id=%s',
            instance_key,
            result.get('fetched'),
            result.get('delivered'),
            result.get('dropped'),
            result.get('failed'),
            result.get('last_id'),
        )
        self._write_temp_sms_result_dump(
            instance_key=instance_key,
            interval_seconds=interval_seconds,
            result=result,
        )

    def _write_temp_sms_result_dump(
        self,
        *,
        instance_key: str,
        interval_seconds: int,
        result: dict[str, Any],
    ) -> None:
        """Write a temporary JSONL dump entry for periodic enterprise SMS sync runs."""
        try:
            self._temp_sms_dump_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                'ts_utc': datetime.now(timezone.utc).isoformat(),
                'instance_key': instance_key,
                'interval_seconds': int(interval_seconds),
                'result': result,
            }
            with self._temp_sms_dump_path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(payload, ensure_ascii=True) + '\n')
        except Exception as exc:
            self._sms_logger.warning('sync.result_dump_failed instance=%s error=%s', instance_key, str(exc))

    async def _update_runtime_state_with_retry(
        self,
        instance_id: str,
        *,
        last_platform_update_id: Optional[str] = None,
        last_error: Optional[str] = None,
        touch_sync: bool = True,
    ) -> bool:
        """Internal helper to update runtime state with retry."""
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            try:
                with SessionLocal() as db:
                    self._instances.update_runtime_state(
                        db,
                        instance_id,
                        last_platform_update_id=last_platform_update_id,
                        last_error=last_error,
                        touch_sync=touch_sync,
                    )
                return True
            except OperationalError as exc:
                if not self._is_sqlite_locked_error(exc):
                    raise
                if attempt == max_attempts:
                    self._logger.error(
                        'runtime_state update skipped after sqlite lock retries instance=%s attempts=%s',
                        instance_id,
                        max_attempts,
                    )
                    return False
                delay = min(0.2 * attempt, 2.0)
                self._logger.warning(
                    'sqlite locked while updating runtime_state instance=%s attempt=%s/%s',
                    instance_id,
                    attempt,
                    max_attempts,
                )
                await asyncio.sleep(delay)

    @staticmethod
    def _is_sqlite_locked_error(exc: OperationalError) -> bool:
        """Internal helper to is sqlite locked error."""
        return 'database is locked' in str(exc).lower()

    def _remember_last_update_id(self, instance_key: str, update_id: Optional[str]) -> None:
        """Keep an in-memory high-water mark so transient DB locks do not replay updates."""
        candidate = str(update_id or '').strip()
        if not candidate:
            return
        current = str(self._last_update_ids.get(instance_key) or '').strip()
        self._last_update_ids[instance_key] = self._merge_update_ids(current, candidate)

    def _merged_last_update_id(self, instance_key: str, persisted_update_id: Optional[str]) -> Optional[str]:
        """Resolve the highest known update id from memory and persisted runtime state."""
        cached = str(self._last_update_ids.get(instance_key) or '').strip()
        persisted = str(persisted_update_id or '').strip()
        merged = self._merge_update_ids(cached, persisted)
        return merged or None

    @staticmethod
    def _merge_update_ids(left: Optional[str], right: Optional[str]) -> str:
        """Choose the higher numeric update id, falling back to the most recent non-empty string."""
        left_text = str(left or '').strip()
        right_text = str(right or '').strip()
        if left_text.isdigit() and right_text.isdigit():
            return str(max(int(left_text), int(right_text)))
        if left_text.isdigit():
            return left_text
        if right_text.isdigit():
            return right_text
        return left_text or right_text

    async def _maybe_handle_local_command(
        self,
        instance_key: str,
        update: dict[str, Any],
        *,
        platform_key: str,
        prompt_cfg: dict[str, Any],
    ) -> bool:
        """Internal helper to maybe handle local command."""
        message = update.get('message') or update.get('edited_message')
        if not isinstance(message, dict):
            return False

        text = str(message.get('text') or '').strip()
        if not text.startswith('/'):
            return False

        command = text.split()[0].split('@')[0].strip().lower()
        chat = message.get('chat') if isinstance(message.get('chat'), dict) else {}
        chat_id = chat.get('id') or chat.get('username') or message.get('chat_id')
        if chat_id is None:
            return False

        connector = connector_registry.get(platform_key)
        if command == '/start':
            await connector.send_text(instance_key, str(chat_id), self._start_message_text(platform_key))
            await self._send_share_phone_prompt_if_needed(
                instance_key,
                platform_key=platform_key,
                chat_id=str(chat_id),
                force=True,
                prompt_cfg=prompt_cfg,
            )
            return True

        if command in {'/share_phone', '/sharephone', '/phone'}:
            await self._send_share_phone_prompt_if_needed(
                instance_key,
                platform_key=platform_key,
                chat_id=str(chat_id),
                force=True,
                prompt_cfg=prompt_cfg,
            )
            return True

        if command in {'/help', '/commands'}:
            await self._send_share_phone_prompt_if_needed(
                instance_key,
                platform_key=platform_key,
                chat_id=str(chat_id),
                force=True,
                prompt_cfg=prompt_cfg,
            )
            return True

        return False

    async def _maybe_send_share_phone_prompt(
        self,
        instance_key: str,
        *,
        platform_key: str,
        chat_id: Optional[str],
        prompt_cfg: dict[str, Any],
    ) -> None:
        """Internal helper to maybe send share phone prompt."""
        if not bool(prompt_cfg.get('enabled')):
            return
        await self._send_share_phone_prompt_if_needed(
            instance_key,
            platform_key=platform_key,
            chat_id=str(chat_id or '').strip(),
            force=False,
            prompt_cfg=prompt_cfg,
        )

    async def _send_share_phone_prompt_if_needed(
        self,
        instance_key: str,
        *,
        platform_key: str,
        chat_id: str,
        force: bool,
        prompt_cfg: dict[str, Any],
    ) -> bool:
        """Internal helper to send share phone prompt if needed."""
        cid = str(chat_id or '').strip()
        if not cid:
            return False
        if not bool(prompt_cfg.get('enabled')):
            return False

        key = (instance_key, cid)
        if not force and key in self._share_phone_prompted:
            return False

        only_if_missing_phone = bool(prompt_cfg.get('only_if_missing_phone', True))
        if only_if_missing_phone:
            should_prompt = await self._should_send_share_phone_prompt(instance_key, cid)
            if should_prompt is False:
                self._share_phone_prompted.add(key)
                return False
            if should_prompt is None:
                return False

        prompt_text = str(prompt_cfg.get('text') or '').strip()
        if not prompt_text:
            return False

        try:
            connector = connector_registry.get(platform_key)
            await connector.send_text(instance_key, cid, prompt_text)
            self._share_phone_prompted.add(key)
            return True
        except Exception as exc:
            self._logger.warning(
                'failed to send share-phone prompt instance=%s chat_id=%s error=%s',
                instance_key,
                cid,
                str(exc),
            )
            return False

    async def _should_send_share_phone_prompt(self, instance_key: str, chat_id: str) -> Optional[bool]:
        """Internal helper to should send share phone prompt."""
        try:
            with SessionLocal() as db:
                has_phone = await self._bridge.chatwoot_contact_has_phone(db, instance_key, chat_id)
        except Exception as exc:
            self._logger.warning(
                'failed to check chatwoot contact phone before prompt instance=%s chat_id=%s error=%s',
                instance_key,
                chat_id,
                str(exc),
            )
            return None

        if has_phone is None:
            return None
        return not has_phone

    def _poll_interval_seconds(self, platform_key: str, platform_metadata: Optional[dict[str, Any]]) -> int:
        """Internal helper to poll interval seconds."""
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        key = str(platform_key or '').strip().lower()
        if key == 'telegram':
            raw = cfg.get('telegram_poll_interval')
            return int(raw or settings.TELEGRAM_POLL_INTERVAL_SECONDS)
        raw = cfg.get('bale_poll_interval')
        return int(raw or settings.BALE_POLL_INTERVAL_SECONDS)

    @staticmethod
    def _long_poll_timeout_seconds(platform_key: str) -> int:
        """Internal helper to long poll timeout seconds."""
        key = str(platform_key or '').strip().lower()
        if key == 'telegram':
            return int(settings.TELEGRAM_LONG_POLL_TIMEOUT_SECONDS)
        return int(settings.BALE_LONG_POLL_TIMEOUT_SECONDS)

    @staticmethod
    def _start_message_text(platform_key: str) -> str:
        """Internal helper to start message text."""
        key = str(platform_key or '').strip().lower()
        if key == 'telegram':
            return str(settings.TELEGRAM_START_MESSAGE_TEXT)
        return str(settings.BALE_START_MESSAGE_TEXT)

    def _share_phone_prompt_config(self, platform_key: str, platform_metadata: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Internal helper to share phone prompt config."""
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        key = str(platform_key or '').strip().lower()
        if key == 'telegram':
            return {
                'enabled': self._coerce_bool(
                    cfg.get('telegram_share_phone_prompt_enabled'),
                    default=settings.TELEGRAM_SHARE_PHONE_BUTTON,
                ),
                'only_if_missing_phone': self._coerce_bool(
                    cfg.get('telegram_share_phone_prompt_only_if_missing_phone'),
                    default=True,
                ),
                'text': str(cfg.get('telegram_share_phone_prompt_text') or settings.TELEGRAM_SHARE_PHONE_PROMPT_TEXT).strip(),
            }

        return {
            'enabled': self._coerce_bool(
                cfg.get('bale_share_phone_prompt_enabled'),
                default=settings.BALE_SHARE_PHONE_BUTTON,
            ),
            'only_if_missing_phone': self._coerce_bool(
                cfg.get('bale_share_phone_prompt_only_if_missing_phone'),
                default=True,
            ),
            'text': str(cfg.get('bale_share_phone_prompt_text') or settings.BALE_SHARE_PHONE_PROMPT_TEXT).strip(),
        }

    @staticmethod
    def _coerce_bool(value: Any, *, default: bool) -> bool:
        """Internal helper to coerce bool."""
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if not text:
            return bool(default)
        if text in {'1', 'true', 'yes', 'on', 'enabled'}:
            return True
        if text in {'0', 'false', 'no', 'off', 'disabled'}:
            return False
        return bool(default)

    async def _platform_update_to_event(
        self,
        instance_key: str,
        platform_key: str,
        update: dict[str, Any],
        *,
        connector: Any,
    ) -> Optional[dict[str, Any]]:
        """Internal helper to platform update to event."""
        message = update.get('message') or update.get('channel_post') or update.get('edited_message')
        if not isinstance(message, dict):
            return None

        chat = message.get('chat') if isinstance(message.get('chat'), dict) else {}
        chat_id = chat.get('id') or chat.get('username') or message.get('chat_id')
        if chat_id is None:
            return None

        sender = message.get('from') if isinstance(message.get('from'), dict) else {}
        from_name = ' '.join([str(sender.get('first_name') or '').strip(), str(sender.get('last_name') or '').strip()]).strip()
        from_name = from_name or sender.get('username')

        text = message.get('text') or message.get('caption') or ''
        if not text:
            text = self._extract_contact_text(message) or ''
        message_id = message.get('message_id') or message.get('id')
        contact_payload = self._extract_contact_payload(message)

        parent_obj = message.get('reply_to_message') if isinstance(message.get('reply_to_message'), dict) else {}
        parent_message_id = parent_obj.get('message_id') or parent_obj.get('id')

        attachments = []
        file_id, filename, content_type_hint = self._extract_file(message)
        if file_id:
            content, content_type, file_path = await connector.download_file_by_id(instance_key, file_id=file_id)
            if content:
                resolved_filename = filename or (str(file_path).split('/')[-1] if file_path else 'file')
                resolved_content_type = self._resolve_attachment_content_type(
                    content_type=content_type,
                    content_type_hint=content_type_hint,
                    filename=resolved_filename,
                    content=content,
                )
                resolved_filename = self._resolve_attachment_filename(
                    filename=resolved_filename,
                    content_type=resolved_content_type,
                )
                attachments.append(
                    {
                        'filename': resolved_filename,
                        'content': content,
                        'content_type': resolved_content_type,
                    }
                )

        return {
            'chat_id': str(chat_id),
            'platform_key': str(platform_key or '').strip().lower() or None,
            'from_name': from_name,
            'text': str(text),
            'message_id': str(message_id) if message_id is not None else None,
            'platform_message_id': str(message_id) if message_id is not None else None,
            'parent_platform_message_id': str(parent_message_id) if parent_message_id is not None else None,
            'attachments': attachments,
            'contact': contact_payload,
        }

    @staticmethod
    def _extract_file(message: dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Internal helper to extract file."""
        doc = message.get('document')
        if isinstance(doc, dict) and doc.get('file_id'):
            return str(doc.get('file_id')), doc.get('file_name'), doc.get('mime_type')

        video = message.get('video')
        if isinstance(video, dict) and video.get('file_id'):
            return str(video.get('file_id')), 'video.mp4', video.get('mime_type') or 'video/mp4'

        photo = message.get('photo')
        if isinstance(photo, list) and photo:
            candidate = photo[-1]
            if isinstance(candidate, dict) and candidate.get('file_id'):
                return str(candidate.get('file_id')), 'photo.jpg', 'image/jpeg'

        voice = message.get('voice')
        if isinstance(voice, dict) and voice.get('file_id'):
            return str(voice.get('file_id')), 'voice.ogg', voice.get('mime_type') or 'audio/ogg'

        audio = message.get('audio')
        if isinstance(audio, dict) and audio.get('file_id'):
            return str(audio.get('file_id')), audio.get('file_name') or 'audio.ogg', audio.get('mime_type')

        return None, None, None

    @staticmethod
    def _extract_contact_text(message: dict[str, Any]) -> Optional[str]:
        """Internal helper to extract contact text."""
        contact = message.get('contact')
        if not isinstance(contact, dict):
            return None

        phone = str(contact.get('phone_number') or '').strip()
        first_name = str(contact.get('first_name') or '').strip()
        last_name = str(contact.get('last_name') or '').strip()
        full_name = ' '.join([first_name, last_name]).strip()
        if not phone:
            return None
        if full_name:
            return f'Shared phone number: {phone} ({full_name})'
        return f'Shared phone number: {phone}'

    @staticmethod
    def _extract_contact_payload(message: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Internal helper to extract contact payload."""
        contact = message.get('contact')
        if not isinstance(contact, dict):
            return None

        phone = str(contact.get('phone_number') or '').strip()
        if not phone:
            return None

        first_name = str(contact.get('first_name') or '').strip() or None
        last_name = str(contact.get('last_name') or '').strip() or None
        user_id = contact.get('user_id')
        return {
            'phone_number': phone,
            'first_name': first_name,
            'last_name': last_name,
            'user_id': str(user_id).strip() if user_id is not None else None,
        }

    @staticmethod
    def _resolve_attachment_content_type(
        *,
        content_type: Optional[str],
        content_type_hint: Optional[str],
        filename: str,
        content: bytes,
    ) -> Optional[str]:
        """Internal helper to resolve attachment content type."""
        raw = str(content_type or '').strip().lower()
        if raw and raw != 'application/octet-stream':
            return raw

        hinted = str(content_type_hint or '').strip().lower()
        if hinted:
            return hinted

        guessed_from_name = mimetypes.guess_type(str(filename or '').strip())[0]
        if guessed_from_name:
            return guessed_from_name.lower()

        return BalePollingService._guess_content_type_from_bytes(content)

    @staticmethod
    def _resolve_attachment_filename(*, filename: str, content_type: Optional[str]) -> str:
        """Internal helper to resolve attachment filename."""
        name = str(filename or '').strip() or 'file'
        if '.' in name.rsplit('/', 1)[-1]:
            return name

        ctype = str(content_type or '').strip().lower()
        if not ctype:
            return name

        ext = BalePollingService._preferred_extension_for_content_type(ctype) or (mimetypes.guess_extension(ctype) or '')
        if ext:
            return f'{name}{ext}'
        return name

    @staticmethod
    def _preferred_extension_for_content_type(content_type: str) -> Optional[str]:
        """Internal helper to preferred extension for content type."""
        mapping = {
            'audio/ogg': '.ogg',
            'audio/mpeg': '.mp3',
            'video/mp4': '.mp4',
            'image/jpeg': '.jpg',
            'image/png': '.png',
            'image/webp': '.webp',
            'image/gif': '.gif',
        }
        return mapping.get(str(content_type or '').strip().lower())

    @staticmethod
    def _guess_content_type_from_bytes(content: bytes) -> Optional[str]:
        """Internal helper to guess content type from bytes."""
        if not content:
            return None
        if content.startswith(b'\x89PNG\r\n\x1a\n'):
            return 'image/png'
        if content.startswith(b'\xff\xd8\xff'):
            return 'image/jpeg'
        if content.startswith((b'GIF87a', b'GIF89a')):
            return 'image/gif'
        if len(content) > 12 and content[:4] == b'RIFF' and content[8:12] == b'WEBP':
            return 'image/webp'
        if content.startswith(b'OggS'):
            return 'audio/ogg'
        if len(content) > 12 and content[:4] == b'RIFF' and content[8:12] == b'WAVE':
            return 'audio/wav'
        if content.startswith(b'ID3') or (len(content) > 1 and content[0] == 0xFF and (content[1] & 0xE0) == 0xE0):
            return 'audio/mpeg'
        if len(content) > 8 and content[4:8] == b'ftyp':
            return 'video/mp4'
        return None

