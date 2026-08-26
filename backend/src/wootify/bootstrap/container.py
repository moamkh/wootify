"""Application composition container used during the incremental refactor."""

from __future__ import annotations

from dataclasses import dataclass, field

from wootify.connectors.registry import ConnectorRegistry
from wootify.plugins.builtins import platform_plugin_registry
from wootify.plugins.registry import PlatformPluginRegistry


@dataclass
class ApplicationContainer:
    """Own process-wide provider registries while services migrate behind ports."""

    plugins: PlatformPluginRegistry = field(default_factory=lambda: platform_plugin_registry)
    connectors: ConnectorRegistry = field(init=False)

    def __post_init__(self) -> None:
        self.connectors = ConnectorRegistry(self.plugins)


__all__ = ["ApplicationContainer"]
