"""Bale polling service — connector lifecycle and update dispatch.

This service owns the long-running poll loop for every active Bale instance
(both ``bale`` bot-API and ``bale_pv``/``bale_pv_enterprise`` userbot).

Responsibilities
----------------
* Start and supervise per-instance polling tasks across server restarts.
* Normalise raw WebSocket/gRPC updates from ``BalePvConnector`` into the
  canonical event shape expected by ``ChatwootBridgeService``.
* Resolve inbound media references (via ``BalePvAdapter.resolve_attachments``)
  before handing events to the bridge.
* Route events to the correct downstream handler:
    - ``bale_pv_enterprise`` → ``EnterpriseBaleService``
    - ``bale_pv``            → ``ChatwootBridgeService`` via ``BalePvAdapter``
    - ``bale`` (legacy bot)  → ``BridgeService`` / ``EnterpriseBaleService``
* Detect and recover from stalled or crashed poll tasks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from sqlalchemy.exc import OperationalError

from app.adapters.bale_pv import BalePvAdapter
from app.config import settings
from app.connectors.registry import connector_registry
from app.db import SessionLocal
from app import runtime_registry
from app.services.bridge_service import BridgeService
from app.services.chatwoot_bridge_service import chatwoot_bridge
from app.services.enterprise_bale_service import EnterpriseBaleService
from app.services.enterprise_telegram_service import EnterpriseTelegramService
from app.services.instance_service import InstanceService


class BalePollingService:
    """Manages polling lifecycle for all active Bale connector instances.

    Instantiated once at application startup (see ``app/main.py``).  Call
    ``start()`` to begin supervising poll tasks and ``stop()`` to shut them
    all down cleanly.
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger("app.services.bale_polling")
        self._sms_logger = logging.getLogger("app.services.enterprise_sms")
        self._stop = asyncio.Event()
        self._manager_task: Optional[asyncio.Task] = None
        self._poll_tasks: dict[str, asyncio.Task] = {}
        self._last_update_ids: dict[str, str] = {}
        self._enterprise_sms_last_run: dict[str, float] = {}
        self._enterprise_sms_enabled_state: dict[str, bool] = {}
        self._enterprise_sms_sync_tasks: dict[str, asyncio.Task] = {}
        self._enterprise_sms_sync_started_at: dict[str, float] = {}
        self._share_phone_prompted: set[tuple[str, str]] = set()
        # Temporary debug dump for periodic enterprise SMS sync results.
        self._temp_sms_dump_path = (
            Path(__file__).resolve().parents[2] / 'data' / 'tmp-enterprise-smoke' / 'sms-sync-results.jsonl'
        )
        self._instances = InstanceService()
        self._bridge = BridgeService()
        self._enterprise = EnterpriseBaleService()
        self._enterprise_telegram = EnterpriseTelegramService()

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
        self._enterprise_sms_sync_started_at.clear()
        for task in self._enterprise_sms_sync_tasks.values():
            if not task.done():
                task.cancel()
        self._enterprise_sms_sync_tasks.clear()
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
                    self._enterprise_sms_last_run.pop(key, None)
                    self._enterprise_sms_enabled_state.pop(key, None)
                    self._enterprise_sms_sync_started_at.pop(key, None)
                    sms_task = self._enterprise_sms_sync_tasks.pop(key, None)
                    if sms_task and not sms_task.done():
                        sms_task.cancel()
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
            self._logger.debug('poll iteration instance=%s', instance_key)
            poll_interval = settings.BALE_POLL_INTERVAL_SECONDS
            runtime_instance_id: Optional[str] = None
            try:
                with SessionLocal() as db:
                    runtime = self._instances.get_runtime_instance(db, instance_key)
                if not runtime or not runtime.instance.is_enabled:
                    await asyncio.sleep(5)
                    continue

                runtime_instance_id = runtime.instance.id
                # Seed in-memory SMS sync timestamp from DB so restarts respect the interval.
                # The DB stores a wall-clock datetime, but we use monotonic time for interval
                # checks. Convert by subtracting the wall-clock elapsed time from the current
                # monotonic value so that comparisons remain consistent.
                if runtime.runtime_state_last_sms_sync_at and instance_key not in self._enterprise_sms_last_run:
                    db_ts = runtime.runtime_state_last_sms_sync_at.timestamp()
                    wall_clock_elapsed = time.time() - db_ts
                    self._enterprise_sms_last_run[instance_key] = time.monotonic() - wall_clock_elapsed
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
                    runtime_instance_id=runtime_instance_id,
                )

                # Register the Bale PV adapter runtime first so inbound normalization
                # and outbound webhooks can use it even if the connector is still
                # completing authentication.
                if platform_key == 'bale_pv_enterprise':
                    try:
                        await runtime_registry.connect_instance(instance_key, platform_key, cfg)
                    except Exception as exc:
                        self._logger.warning(
                            'bale_pv_adapter_register_failed instance=%s error=%s',
                            instance_key,
                            exc,
                        )

                await connector.connect(instance_key, cfg, runtime.proxy)

                offset = None
                last_update = self._merged_last_update_id(instance_key, runtime.runtime_state_last_update_id)
                if last_update and str(last_update).isdigit():
                    offset = int(last_update) + 1

                self._logger.debug('poll get_updates instance=%s offset=%s timeout=%s', instance_key, offset, long_poll_timeout)
                resp = await connector.get_updates(instance_key, offset=offset, timeout=long_poll_timeout)
                self._logger.debug('poll get_updates_done instance=%s ok=%s result_count=%s', instance_key, resp.get('ok') if isinstance(resp, dict) else None, len(resp.get('result', [])) if isinstance(resp, dict) else None)
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

                    update_processed = False
                    # Rate-limit: small pause between updates to avoid hammering Chatwoot
                    await asyncio.sleep(0.5)

                    if platform_key == 'bale_enterprise':
                        try:
                            with SessionLocal() as db:
                                await self._enterprise.handle_platform_update(db, instance_key, update)
                            update_processed = True
                        except Exception as exc:
                            self._logger.error(
                                'enterprise_update_error instance=%s update_id=%s error_type=%s error=%s',
                                instance_key,
                                processed_update_id,
                                type(exc).__name__,
                                str(exc),
                                exc_info=True,
                            )
                            # Do NOT mark as processed on failure to avoid message loss.
                    elif platform_key == 'telegram_enterprise':
                        try:
                            with SessionLocal() as db:
                                await self._enterprise_telegram.handle_platform_update(db, instance_key, update)
                            update_processed = True
                        except Exception as exc:
                            self._logger.error(
                                'enterprise_telegram_update_error instance=%s update_id=%s error_type=%s error=%s',
                                instance_key,
                                processed_update_id,
                                type(exc).__name__,
                                str(exc),
                                exc_info=True,
                            )
                            # Do NOT mark as processed on failure to avoid message loss.
                    else:
                        handled = await self._maybe_handle_local_command(
                            instance_key,
                            update,
                            platform_key=platform_key,
                            prompt_cfg=prompt_cfg,
                        )
                        if handled:
                            update_processed = True
                        else:
                            if platform_key == 'bale_pv_enterprise':
                                event = await self._normalize_with_adapter(instance_key, update)
                            else:
                                event = await self._platform_update_to_event(instance_key, platform_key, update, connector=connector)
                            self._logger.debug('poll normalized instance=%s update_id=%s event=%s', instance_key, processed_update_id, bool(event))
                            if not event:
                                update_processed = True
                            else:
                                try:
                                    with SessionLocal() as db:
                                        if platform_key == 'bale_pv_enterprise':
                                            await chatwoot_bridge.ingest_platform_event(db, instance_key, event)
                                        else:
                                            await self._bridge.ingest_platform_event(db, instance_key, event)
                                    self._logger.debug('poll bridge_ingest_ok instance=%s update_id=%s', instance_key, processed_update_id)
                                except Exception as exc:
                                    self._logger.error(
                                        'bridge_ingest_error instance=%s update_id=%s error_type=%s error=%s',
                                        instance_key,
                                        processed_update_id,
                                        type(exc).__name__,
                                        str(exc),
                                        exc_info=True,
                                    )
                                # Mark as processed even on bridge failure to avoid infinite replay.
                                update_processed = True

                    if processed_update_id and update_processed:
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
        runtime_instance_id: Optional[str] = None,
    ) -> None:
        """Trigger enterprise SMS sync when the configured interval has elapsed.

        Runs the actual sync in a background task so that message polling
        is not blocked by slow SMS API calls.  A stuck or hung task is
        detected and cancelled so that a single failure cannot block the
        periodic sync forever.
        """
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
        now = time.monotonic()
        last_run = float(self._enterprise_sms_last_run.get(instance_key, 0.0))
        if last_run and (now - last_run) < float(interval_seconds):
            self._sms_logger.debug(
                'sync.skip instance=%s reason=interval_not_elapsed elapsed=%ss interval=%ss',
                instance_key,
                int(now - last_run),
                interval_seconds,
            )
            return

        # If a sync is already running for this instance, check whether it has
        # exceeded a reasonable maximum runtime.  A hung task would otherwise
        # block all future syncs forever.
        max_sync_runtime_seconds = max(300, interval_seconds * 2)
        existing_task = self._enterprise_sms_sync_tasks.get(instance_key)
        if existing_task and not existing_task.done():
            started_at = self._enterprise_sms_sync_started_at.get(instance_key, now)
            elapsed = now - started_at
            if elapsed < max_sync_runtime_seconds:
                self._sms_logger.debug(
                    'sync.skip instance=%s reason=already_running runtime=%ss max=%ss',
                    instance_key,
                    int(elapsed),
                    max_sync_runtime_seconds,
                )
                return
            self._sms_logger.warning(
                'sync.cancel_stuck instance=%s runtime=%ss max=%ss',
                instance_key,
                int(elapsed),
                max_sync_runtime_seconds,
            )
            existing_task.cancel()
            self._enterprise_sms_sync_tasks.pop(instance_key, None)
            self._enterprise_sms_sync_started_at.pop(instance_key, None)

        self._enterprise_sms_last_run[instance_key] = now
        self._enterprise_sms_sync_started_at[instance_key] = now
        self._enterprise_sms_sync_tasks[instance_key] = asyncio.create_task(
            self._run_enterprise_sms_sync(
                instance_key=instance_key,
                interval_seconds=interval_seconds,
                runtime_instance_id=runtime_instance_id,
            )
        )
        self._sms_logger.info(
            'sync.scheduled instance=%s interval=%ss max_runtime=%ss',
            instance_key,
            interval_seconds,
            max_sync_runtime_seconds,
        )

    async def _run_enterprise_sms_sync(
        self,
        instance_key: str,
        *,
        interval_seconds: int,
        runtime_instance_id: Optional[str] = None,
    ) -> None:
        """Execute SMS sync and persist timestamp.

        A hard timeout is applied to the whole operation so that a stuck DB
        session or a hung SMS API call cannot block future syncs indefinitely.
        """
        # The overall operation should not run longer than twice the configured
        # interval; this leaves headroom for slow APIs while still preventing
        # a runaway task from blocking the scheduler.
        overall_timeout_seconds = max(300, interval_seconds * 2)
        try:
            async with asyncio.timeout(overall_timeout_seconds):
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
                await self._write_temp_sms_result_dump(
                    instance_key=instance_key,
                    interval_seconds=interval_seconds,
                    result=result,
                )
        except asyncio.TimeoutError:
            self._sms_logger.error(
                'sync.timeout instance=%s timeout_seconds=%s',
                instance_key,
                overall_timeout_seconds,
            )
        except Exception as exc:
            self._sms_logger.error(
                'sync.failed instance=%s error_type=%s error=%s',
                instance_key,
                type(exc).__name__,
                str(exc),
                exc_info=True,
            )
        finally:
            # Persist timestamp to DB so restarts don't re-sync immediately.
            if runtime_instance_id:
                try:
                    from datetime import datetime, timezone
                    await self._update_runtime_state_with_retry(
                        runtime_instance_id,
                        last_enterprise_sms_sync_at=datetime.now(timezone.utc),
                        touch_sync=False,
                    )
                except Exception as exc:
                    self._sms_logger.warning(
                        'sync.timestamp_persist_failed instance=%s error=%s',
                        instance_key,
                        str(exc),
                    )

    async def _write_temp_sms_result_dump(
        self,
        *,
        instance_key: str,
        interval_seconds: int,
        result: dict[str, Any],
    ) -> None:
        """Write a temporary JSONL dump entry for periodic enterprise SMS sync runs."""
        def _write() -> None:
            self._temp_sms_dump_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                'ts_utc': datetime.now(timezone.utc).isoformat(),
                'instance_key': instance_key,
                'interval_seconds': int(interval_seconds),
                'result': result,
            }
            with self._temp_sms_dump_path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(payload, ensure_ascii=True) + '\n')

        try:
            await asyncio.to_thread(_write)
        except Exception as exc:
            self._sms_logger.warning('sync.result_dump_failed instance=%s error=%s', instance_key, str(exc))

    async def _update_runtime_state_with_retry(
        self,
        instance_id: str,
        *,
        last_platform_update_id: Optional[str] = None,
        last_error: Optional[str] = None,
        touch_sync: bool = True,
        last_enterprise_sms_sync_at: Optional[datetime] = None,
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
                        last_enterprise_sms_sync_at=last_enterprise_sms_sync_at,
                    )
                self._logger.debug('runtime_state_updated instance=%s last_error=%s touch_sync=%s', instance_id, last_error, touch_sync)
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
            try:
                await connector.send_text(instance_key, str(chat_id), self._start_message_text(platform_key))
            except Exception as exc:
                self._logger.warning(
                    'local_command send_text failed instance=%s chat_id=%s command=%s error_type=%s error=%s',
                    instance_key,
                    chat_id,
                    command,
                    type(exc).__name__,
                    str(exc),
                )
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
        if key == 'bale_pv_enterprise':
            raw = cfg.get('bale_pv_poll_interval')
            return int(raw or settings.BALE_POLL_INTERVAL_SECONDS)
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

        if key == 'bale_pv_enterprise':
            return {
                'enabled': self._coerce_bool(
                    cfg.get('bale_pv_share_phone_prompt_enabled'),
                    default=settings.BALE_SHARE_PHONE_BUTTON,
                ),
                'only_if_missing_phone': self._coerce_bool(
                    cfg.get('bale_pv_share_phone_prompt_only_if_missing_phone'),
                    default=True,
                ),
                'text': str(cfg.get('bale_pv_share_phone_prompt_text') or settings.BALE_SHARE_PHONE_PROMPT_TEXT).strip(),
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

    async def _normalize_with_adapter(
        self,
        instance_key: str,
        update: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Normalize a Bale PV update using the new adapter runtime."""
        from app.runtime_registry import get_runtime
        runtime = get_runtime(instance_key)
        if not runtime:
            self._logger.warning('bale_pv_adapter_no_runtime instance=%s', instance_key)
            return None
        event = runtime.adapter.normalize_incoming_update(update)
        if not event:
            self._logger.debug('bale_pv_adapter_normalize_skipped instance=%s', instance_key)
            return None

        original_refs = event.get("attachments") or []
        if original_refs:
            self._logger.info(
                'bale_pv_adapter_normalizing_attachments instance=%s message_id=%s refs=%s',
                instance_key,
                event.get('message_id'),
                [
                    {'filename': ref.get('filename'), 'content_type': ref.get('content_type'), 'file_id': str(ref.get('file_id', ''))[:80]}
                    for ref in original_refs
                ],
            )
            try:
                event["attachments"] = await runtime.adapter.resolve_attachments(event["attachments"])
            except Exception as exc:
                self._logger.warning(
                    'bale_pv_adapter_resolve_attachments_failed instance=%s error=%s',
                    instance_key,
                    exc,
                    exc_info=True,
                )
                event["attachments"] = []
            if original_refs and not event.get("attachments"):
                self._logger.warning(
                    'bale_pv_adapter_attachments_dropped instance=%s message_id=%s original_count=%s',
                    instance_key,
                    event.get('message_id'),
                    len(original_refs),
                )
        return event

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

        chat_type = str(chat.get('type') or 'private').strip().lower() or 'private'
        # For groups/channels the contact in Chatwoot should be named after the
        # group/channel, not the individual sender, so the conversation is identifiable.
        if chat_type in ('group', 'channel'):
            chat_title = chat.get('title')
            if chat_title:
                from_name = str(chat_title).strip()

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
                resolved_content_type = BalePvAdapter._normalize_content_type(
                    filename=resolved_filename,
                    content_type=content_type or content_type_hint,
                    content=content,
                )
                # Convert WEBP stickers to JPEG/PNG so Chatwoot can render them.
                if resolved_content_type == 'image/webp':
                    converted, ext, converted_ct = BalePvAdapter._convert_webp(content)
                    if converted and ext and converted_ct:
                        content = converted
                        resolved_content_type = converted_ct
                        resolved_filename = str(resolved_filename).rsplit('.', 1)[0] + ext
                    else:
                        self._logger.warning(
                            'bale_polling webp_conversion_failed instance=%s filename=%s',
                            instance_key,
                            resolved_filename,
                        )
                resolved_filename = BalePvAdapter._normalize_filename_extension(
                    resolved_filename, resolved_content_type
                )
                attachments.append(
                    {
                        'filename': resolved_filename,
                        'content': content,
                        'content_type': resolved_content_type,
                    }
                )

        event: dict[str, Any] = {
            'chat_id': str(chat_id),
            'platform_key': str(platform_key or '').strip().lower() or None,
            'chat_type': str(chat.get('type') or 'private').strip().lower() or 'private',
            'from_name': from_name,
            'text': str(text),
            'message_id': str(message_id) if message_id is not None else None,
            'platform_message_id': str(message_id) if message_id is not None else None,
            'parent_platform_message_id': str(parent_message_id) if parent_message_id is not None else None,
            'attachments': attachments,
            'contact': contact_payload,
        }
        if message.get('_outgoing'):
            event['outgoing'] = True
        if message.get('_deleted'):
            event['_deleted'] = True
        return event

    @staticmethod
    def _extract_file(
        message: dict[str, Any],
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Extract a single Bale attachment reference from a message."""
        doc = message.get("document")
        if isinstance(doc, dict) and doc.get("file_id"):
            return str(doc.get("file_id")), doc.get("file_name"), doc.get("mime_type")

        video = message.get("video")
        if isinstance(video, dict) and video.get("file_id"):
            return (
                str(video.get("file_id")),
                "video.mp4",
                video.get("mime_type") or "video/mp4",
            )

        photo = message.get("photo")
        if isinstance(photo, list) and photo:
            candidate = photo[-1]
            if isinstance(candidate, dict) and candidate.get("file_id"):
                return str(candidate.get("file_id")), "photo.jpg", "image/jpeg"

        audio = message.get("audio")
        if isinstance(audio, dict) and audio.get("file_id"):
            return (
                str(audio.get("file_id")),
                audio.get("file_name") or "audio.ogg",
                audio.get("mime_type"),
            )

        voice = message.get("voice")
        if isinstance(voice, dict) and voice.get("file_id"):
            return (
                str(voice.get("file_id")),
                "voice.ogg",
                voice.get("mime_type") or "audio/ogg",
            )

        sticker = message.get("sticker")
        if isinstance(sticker, dict) and sticker.get("file_id"):
            thumbnail = sticker.get("thumbnail")
            thumb_id = thumbnail.get("file_id") if isinstance(thumbnail, dict) else None
            return str(thumb_id or sticker.get("file_id")), "sticker.webp", "image/webp"

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
