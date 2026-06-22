"""In-memory registry of connected platform adapter runtimes.

Follows the same pattern as evolution-api/messenger_chatwoot_connector but is
async-aware and supports the existing wootify_instance_manager models.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.adapters.base import BasePlatformAdapter
from app.adapters.bale_pv import BalePvAdapter

logger = logging.getLogger("app.runtime_registry")


@dataclass
class InstanceRuntime:
    instance_key: str
    platform_type: str
    adapter: BasePlatformAdapter
    status: str


class InstanceRuntimeRegistry:
    def __init__(self) -> None:
        self._by_key: Dict[str, InstanceRuntime] = {}

    def get(self, instance_key: str) -> Optional[InstanceRuntime]:
        return self._by_key.get(instance_key)

    def set(self, runtime: InstanceRuntime) -> None:
        self._by_key[runtime.instance_key] = runtime

    def delete(self, instance_key: str) -> Optional[InstanceRuntime]:
        return self._by_key.pop(instance_key, None)

    def list(self) -> list[InstanceRuntime]:
        return list(self._by_key.values())


registry = InstanceRuntimeRegistry()


def _build_adapter(platform_type: str, instance_key: str, config: Dict[str, Any]) -> BasePlatformAdapter:
    if platform_type == "bale_pv_enterprise":
        return BalePvAdapter(instance_key, config)
    raise ValueError(f"Unsupported platform_type for adapter registry: {platform_type}")


async def connect_instance(instance_key: str, platform_type: str, config: Dict[str, Any]) -> InstanceRuntime:
    existing = registry.get(instance_key)
    if existing and existing.status == "open":
        return existing

    adapter = _build_adapter(platform_type, instance_key, config)
    await adapter.connect()
    runtime = InstanceRuntime(
        instance_key=instance_key,
        platform_type=platform_type,
        adapter=adapter,
        status="open",
    )
    registry.set(runtime)
    logger.info("runtime_registry_connected instance=%s platform=%s", instance_key, platform_type)
    return runtime


async def disconnect_instance(instance_key: str) -> None:
    runtime = registry.delete(instance_key)
    if runtime:
        try:
            await runtime.adapter.disconnect()
        except Exception as exc:
            logger.warning("runtime_registry_disconnect_error instance=%s error=%s", instance_key, exc)


def get_runtime(instance_key: str) -> Optional[InstanceRuntime]:
    return registry.get(instance_key)
