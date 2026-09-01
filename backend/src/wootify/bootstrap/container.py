"""Application composition container used during the incremental refactor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial

from wootify.connectors.registry import ConnectorRegistry
from wootify.plugins.instagram.polling_service import InstagramPollingService
from wootify.plugins.builtins import platform_plugin_registry
from wootify.plugins.registry import PlatformPluginRegistry
from wootify.application.messaging.polling import BalePollingService
from wootify.application.instances.platform_catalog import PlatformRegistryService
from wootify.infrastructure.persistence.session import SessionLocal
from wootify.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork


@dataclass
class ApplicationContainer:
    """Own process-wide provider registries while services migrate behind ports."""

    plugins: PlatformPluginRegistry = field(default_factory=lambda: platform_plugin_registry)
    connectors: ConnectorRegistry = field(init=False)
    polling: BalePollingService = field(default_factory=BalePollingService)
    instagram_polling: InstagramPollingService = field(default_factory=InstagramPollingService)
    platform_catalog: PlatformRegistryService = field(default_factory=PlatformRegistryService)
    unit_of_work_factory: Callable[[], SqlAlchemyUnitOfWork] = field(
        default_factory=lambda: partial(SqlAlchemyUnitOfWork, SessionLocal)
    )

    def __post_init__(self) -> None:
        self.connectors = ConnectorRegistry(self.plugins)


__all__ = ["ApplicationContainer"]
