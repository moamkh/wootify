"""Instagram PV polling service — connector lifecycle and update dispatch.

Self-contained counterpart to ``BalePollingService`` for
``instagram_pv_enterprise`` instances. Kept in the ``app.instagram`` package so
Instagram delivery logic never entangles the Bale service.

Responsibilities
----------------
* Start and supervise one poll task per enabled Instagram PV instance.
* Register the ``InstagramPvAdapter`` runtime (``runtime_registry``) so the
  Chatwoot webhook path can deliver outbound agent messages.
* Normalize inbound updates through the adapter and resolve attachments.
* Deliver events to ``ChatwootBridgeService``.
* Failure handling with a per-instance in-memory retry queue (head-of-line
  blocking, bounded backoff). Connector watermarks are only committed after
  successful delivery, so a restart re-fetches anything not yet delivered
  (at-least-once semantics without touching the shared ``inbound_event_retries``
  table, which the Bale drainer owns).

Non-overlap contract
--------------------
* This service only ever polls instances whose platform key is
  ``instagram_pv_enterprise``; ``BalePollingService`` skips those instances.
* Exactly one poll task per instance key (manager-reconciled).
* All connector client calls are serialized per instance by the connector's
  own ``asyncio.Lock``; nothing blocking runs on the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Optional

import httpx
from sqlalchemy.exc import OperationalError

from wootify import runtime_registry
from wootify.config import settings
from wootify.infrastructure.persistence.session import SessionLocal
from wootify.plugins.instagram.connector import instagram_pv
from wootify.application.messaging.chatwoot_bridge import chatwoot_bridge
from wootify.application.instances.service import InstanceService


class InstagramPollingService:
    """Manages polling lifecycle for all active Instagram PV instances.

    Instantiated once by the application lifecycle. Call
    ``start()`` to begin supervising poll tasks and ``stop()`` to shut them
    all down cleanly.
    """

    PLATFORM_KEY = "instagram_pv_enterprise"

    # How many failures trigger the maximum retry backoff (never a drop).
    _MAX_UPDATE_ATTEMPTS = 3
    # Backoff between retry-queue drain attempts after a failure.
    _RETRY_BACKOFF_BASE_SECONDS = 15.0
    _RETRY_BACKOFF_MAX_SECONDS = 120.0

    def __init__(self) -> None:
        self._logger = logging.getLogger("app.instagram.polling")
        self._stop = asyncio.Event()
        self._manager_task: Optional[asyncio.Task] = None
        self._poll_tasks: dict[str, asyncio.Task] = {}
        self._last_update_ids: dict[str, str] = {}
        # instance_key -> FIFO of updates awaiting redelivery.
        self._pending_events: dict[str, deque[dict[str, Any]]] = {}
        # instance_key -> monotonic timestamp of the next allowed drain attempt.
        self._retry_not_before: dict[str, float] = {}
        # instance_key -> {update_id: attempts}
        self._update_fail_counts: dict[str, dict[str, int]] = {}
        self._instances = InstanceService()

    async def start(self) -> None:
        """Start supervising Instagram PV instance poll tasks."""
        if self._manager_task and not self._manager_task.done():
            return
        self._stop.clear()
        self._manager_task = asyncio.create_task(self._run_manager())
        self._logger.info("started")

    async def stop(self) -> None:
        """Stop all poll tasks and release connector resources."""
        self._stop.set()
        poll_tasks = list(self._poll_tasks.values())
        for task in poll_tasks:
            task.cancel()
        self._poll_tasks.clear()
        self._last_update_ids.clear()
        self._pending_events.clear()
        self._retry_not_before.clear()
        self._update_fail_counts.clear()
        if self._manager_task:
            self._manager_task.cancel()
            try:
                await self._manager_task
            except asyncio.CancelledError:
                pass
            self._manager_task = None
        pending = [task for task in poll_tasks if not task.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._logger.info("stopped")

    # ------------------------------------------------------------------
    # Manager
    # ------------------------------------------------------------------

    async def _run_manager(self) -> None:
        """Reconcile enabled Instagram instances against running poll tasks."""
        while not self._stop.is_set():
            try:
                enabled = self._list_enabled_instance_keys()
                for key, task in list(self._poll_tasks.items()):
                    if task.done():
                        self._poll_tasks.pop(key)
                existing = set(self._poll_tasks.keys())

                disabled_tasks = []
                for key in existing - enabled:
                    task = self._poll_tasks.pop(key)
                    task.cancel()
                    disabled_tasks.append(task)
                    self._last_update_ids.pop(key, None)
                    self._pending_events.pop(key, None)
                    self._retry_not_before.pop(key, None)
                    self._update_fail_counts.pop(key, None)
                    self._logger.info("stopped polling instance=%s", key)
                if disabled_tasks:
                    await asyncio.gather(*disabled_tasks, return_exceptions=True)
                for key in existing - enabled:
                    await runtime_registry.disconnect_instance(key)

                for key in enabled - existing:
                    self._poll_tasks[key] = asyncio.create_task(self._run_instance(key))
                    self._logger.info("started polling instance=%s", key)
            except Exception as exc:
                self._logger.exception("manager loop failed: %s", exc)

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=10)
            except asyncio.TimeoutError:
                continue

    def _list_enabled_instance_keys(self) -> set[str]:
        """List enabled instance keys whose platform is Instagram PV."""
        with SessionLocal() as db:
            runtimes = self._instances.list_runtime_enabled_instances(db)
            return {
                runtime.instance.instance_key
                for runtime in runtimes
                if str(runtime.platform_type.key or "").strip().lower() == self.PLATFORM_KEY
            }

    # ------------------------------------------------------------------
    # Per-instance poll loop
    # ------------------------------------------------------------------

    async def _run_instance(self, instance_key: str) -> None:
        """Poll one Instagram PV instance until stopped or disabled."""
        while not self._stop.is_set():
            runtime_instance_id: Optional[str] = None
            poll_interval = int(settings.INSTAGRAM_PV_POLL_INTERVAL_SECONDS)
            try:
                with SessionLocal() as db:
                    runtime = self._instances.get_runtime_instance(db, instance_key)
                if not runtime or not runtime.instance.is_enabled:
                    await asyncio.sleep(5)
                    continue

                runtime_instance_id = runtime.instance.id
                cfg = runtime.platform_metadata
                raw_interval = cfg.get("instagram_poll_interval")
                poll_interval = int(raw_interval or settings.INSTAGRAM_PV_POLL_INTERVAL_SECONDS)
                long_poll_timeout = int(settings.INSTAGRAM_PV_LONG_POLL_TIMEOUT_SECONDS)

                # Connect the connector first (carries the proxy config), then
                # register the adapter runtime; adapter.connect() early-returns
                # once the connector is authenticated.
                try:
                    await instagram_pv.connect(instance_key, cfg, runtime.proxy)
                except Exception as exc:
                    await self._update_runtime_state_with_retry(
                        runtime_instance_id,
                        last_error=f"connect_failed: {exc}",
                        touch_sync=False,
                    )
                    await asyncio.sleep(min(max(poll_interval, 10), 60))
                    continue

                try:
                    await runtime_registry.connect_instance(
                        instance_key, self.PLATFORM_KEY, {**cfg, "proxy": runtime.proxy}
                    )
                except Exception as exc:
                    self._logger.warning(
                        "instagram_adapter_register_failed instance=%s error=%s",
                        instance_key,
                        exc,
                    )

                # Redeliver queued events before fetching new ones; inbound
                # fetching pauses while the queue is non-empty so connector
                # watermarks stay behind the queue (no duplicates, no loss).
                queue_drained = await self._drain_pending(instance_key, runtime_instance_id)
                if not queue_drained:
                    await asyncio.sleep(2)
                    continue

                resp = await instagram_pv.get_updates(
                    instance_key, timeout=long_poll_timeout
                )
                if not isinstance(resp, dict) or not resp.get("ok"):
                    await self._update_runtime_state_with_retry(
                        runtime_instance_id,
                        last_error=str(
                            (resp or {}).get("description") or "instagram_get_updates_failed"
                        ),
                        touch_sync=False,
                    )
                    await asyncio.sleep(5)
                    continue

                updates = resp.get("result") or []
                batch_ok = True
                for index, update in enumerate(updates):
                    if not isinstance(update, dict):
                        continue
                    update_id = update.get("update_id")
                    processed_update_id = (
                        str(int(str(update_id)))
                        if update_id is not None and str(update_id).isdigit()
                        else None
                    )
                    try:
                        delivered = await self._deliver_update(instance_key, update)
                    except Exception as exc:
                        delivered = False
                        self._logger.error(
                            "instagram_ingest_error instance=%s update_id=%s error_type=%s error=%s",
                            instance_key,
                            processed_update_id,
                            type(exc).__name__,
                            str(exc),
                            exc_info=True,
                        )

                    if delivered:
                        self._reset_update_failure(instance_key, processed_update_id)
                        if processed_update_id:
                            self._remember_last_update_id(instance_key, processed_update_id)
                            await self._update_runtime_state_with_retry(
                                runtime_instance_id,
                                last_platform_update_id=processed_update_id,
                                last_error=None,
                                touch_sync=True,
                            )
                        continue

                    # Delivery failed: bounded attempts, then queue the failed
                    # update plus the rest of the batch for redelivery and stop
                    # the batch so per-chat ordering is preserved.
                    batch_ok = False
                    self._record_update_failure(instance_key, processed_update_id)
                    queue = self._pending_events.setdefault(instance_key, deque())
                    queue.append(update)
                    for remaining in updates[index + 1 :]:
                        if isinstance(remaining, dict):
                            queue.append(remaining)
                    self._retry_not_before[instance_key] = (
                        time.monotonic() + self._RETRY_BACKOFF_BASE_SECONDS
                    )
                    break

                # Only commit watermarks when the whole batch (and the retry
                # queue) was delivered; otherwise uncommitted messages are
                # re-fetched after a restart.
                if batch_ok and not self._pending_events.get(instance_key):
                    await instagram_pv.commit_updates(instance_key)
                    await self._update_runtime_state_with_retry(
                        runtime_instance_id,
                        last_error=None,
                        touch_sync=True,
                    )

                if updates:
                    continue
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=min(float(poll_interval), 2.0)
                    )
                except asyncio.TimeoutError:
                    continue

            except asyncio.CancelledError:
                return
            except httpx.RequestError as exc:
                self._logger.warning(
                    "instagram_poll transport_error instance=%s error_type=%s error=%s",
                    instance_key,
                    type(exc).__name__,
                    str(exc),
                )
                await self._safe_record_error(instance_key, runtime_instance_id, exc)
                await asyncio.sleep(5)
                continue
            except Exception as exc:
                self._logger.exception(
                    "instagram_poll error instance=%s error=%s", instance_key, exc
                )
                await self._safe_record_error(instance_key, runtime_instance_id, exc)
                await asyncio.sleep(5)
                continue

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    async def _deliver_update(self, instance_key: str, update: dict[str, Any]) -> bool:
        """Normalize one update through the adapter and ingest it to Chatwoot."""
        runtime = runtime_registry.get_runtime(instance_key)
        if not runtime:
            self._logger.warning("instagram_adapter_no_runtime instance=%s", instance_key)
            return False
        event = runtime.adapter.normalize_incoming_update(update)
        if not event:
            # Nothing worth bridging (self echoes, unsupported items): count as
            # delivered so the watermark can advance past it.
            return True
        if event.get("attachments"):
            try:
                event["attachments"] = await runtime.adapter.resolve_attachments(
                    event["attachments"]
                )
            except Exception as exc:
                self._logger.warning(
                    "instagram_attachments_failed instance=%s message_id=%s error=%s",
                    instance_key,
                    event.get("message_id"),
                    exc,
                    exc_info=True,
                )
                return False
        with SessionLocal() as db:
            result = await chatwoot_bridge.ingest_platform_event(db, instance_key, event)
        return bool(result.get("ok"))

    async def _drain_pending(self, instance_key: str, runtime_instance_id: Optional[str]) -> bool:
        """Redeliver queued events in order; False while backlog remains."""
        queue = self._pending_events.get(instance_key)
        if not queue:
            return True

        not_before = self._retry_not_before.get(instance_key, 0.0)
        if time.monotonic() < not_before:
            return False

        while queue:
            update = queue[0]
            update_id = update.get("update_id")
            processed_update_id = (
                str(int(str(update_id)))
                if update_id is not None and str(update_id).isdigit()
                else None
            )
            try:
                delivered = await self._deliver_update(instance_key, update)
            except Exception as exc:
                delivered = False
                self._logger.error(
                    "instagram_retry_error instance=%s update_id=%s error_type=%s error=%s",
                    instance_key,
                    processed_update_id,
                    type(exc).__name__,
                    str(exc),
                )
            if not delivered:
                if self._record_update_failure(instance_key, processed_update_id):
                    self._logger.error(
                        "instagram_update_retrying instance=%s update_id=%s attempts=%s",
                        instance_key,
                        processed_update_id,
                        self._MAX_UPDATE_ATTEMPTS,
                    )
                    # Preserve the failed event and its watermark. A long
                    # outage must not turn into silent permanent message loss.
                    self._retry_not_before[instance_key] = time.monotonic() + self._RETRY_BACKOFF_MAX_SECONDS
                    await self._update_runtime_state_with_retry(runtime_instance_id, last_error="Instagram delivery blocked; retrying", touch_sync=False)
                    return False
                attempts = sum(self._update_fail_counts.get(instance_key, {}).values())
                backoff = min(
                    self._RETRY_BACKOFF_BASE_SECONDS * max(attempts, 1),
                    self._RETRY_BACKOFF_MAX_SECONDS,
                )
                self._retry_not_before[instance_key] = time.monotonic() + backoff
                return False
            queue.popleft()
            self._reset_update_failure(instance_key, processed_update_id)
            if processed_update_id:
                self._remember_last_update_id(instance_key, processed_update_id)
                await self._update_runtime_state_with_retry(
                    runtime_instance_id,
                    last_platform_update_id=processed_update_id,
                    last_error=None,
                    touch_sync=True,
                )

        self._pending_events.pop(instance_key, None)
        self._retry_not_before.pop(instance_key, None)
        await instagram_pv.commit_updates(instance_key)
        return True

    # ------------------------------------------------------------------
    # Bookkeeping helpers
    # ------------------------------------------------------------------

    def _record_update_failure(self, instance_key: str, update_id: Optional[str]) -> bool:
        """Count a delivery failure; True when the attempt budget is exhausted."""
        if not update_id:
            return True
        counts = self._update_fail_counts.setdefault(instance_key, {})
        attempts = counts.get(update_id, 0) + 1
        if attempts >= self._MAX_UPDATE_ATTEMPTS:
            counts.pop(update_id, None)
            return True
        counts[update_id] = attempts
        return False

    def _reset_update_failure(self, instance_key: str, update_id: Optional[str]) -> None:
        """Clear the failure counter for an update that finally delivered."""
        if not update_id:
            return
        counts = self._update_fail_counts.get(instance_key)
        if counts:
            counts.pop(update_id, None)

    def _remember_last_update_id(self, instance_key: str, update_id: Optional[str]) -> None:
        """Keep an in-memory high-water mark so transient DB locks do not replay updates."""
        candidate = str(update_id or "").strip()
        if not candidate:
            return
        current = str(self._last_update_ids.get(instance_key) or "").strip()
        if current.isdigit() and candidate.isdigit():
            self._last_update_ids[instance_key] = str(max(int(current), int(candidate)))
        else:
            self._last_update_ids[instance_key] = candidate

    async def _safe_record_error(
        self,
        instance_key: str,
        runtime_instance_id: Optional[str],
        exc: Exception,
    ) -> None:
        """Best-effort last_error persistence for loop-level failures."""
        if not runtime_instance_id:
            try:
                with SessionLocal() as db:
                    runtime = self._instances.get_runtime_instance(db, instance_key)
                    runtime_instance_id = runtime.instance.id if runtime else None
            except Exception:
                runtime_instance_id = None
        if runtime_instance_id:
            await self._update_runtime_state_with_retry(
                runtime_instance_id,
                last_error=f"{type(exc).__name__}: {exc}",
                touch_sync=False,
            )

    async def _update_runtime_state_with_retry(
        self,
        instance_id: Optional[str],
        *,
        last_platform_update_id: Optional[str] = None,
        last_error: Optional[str] = None,
        touch_sync: bool = True,
    ) -> bool:
        """Persist runtime state, retrying through transient SQLite locks."""
        if not instance_id:
            return False
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
                if "database is locked" not in str(exc).lower():
                    raise
                if attempt == max_attempts:
                    self._logger.error(
                        "runtime_state update skipped after sqlite lock retries instance=%s",
                        instance_id,
                    )
                    return False
                await asyncio.sleep(min(0.2 * attempt, 2.0))
        return False


instagram_polling_service = InstagramPollingService()
