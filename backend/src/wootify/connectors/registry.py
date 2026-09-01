"""
Module Overview
---------------
Purpose: Platform connector implementations and connector registry abstractions.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from wootify.connectors.base_connector import PlatformConnector
from wootify.plugins.builtins import platform_plugin_registry
from wootify.plugins.registry import PlatformPluginRegistry


# Compatibility export; plugin descriptors are now the source of truth.
CONNECTOR_SOURCE_PREFIX = {
    plugin.key: plugin.source_prefix for plugin in platform_plugin_registry
}


class ConnectorRegistry:
    """Represents connector registry."""
    def __init__(self, plugins: PlatformPluginRegistry = platform_plugin_registry) -> None:
        """Initialize the instance."""
        self._plugins = plugins

    def get(self, platform_key: str) -> PlatformConnector:
        """Get connector implementation for a platform key."""
        try:
            return self._plugins.get(platform_key).connector
        except ValueError as exc:
            raise ValueError(f"Unsupported platform connector '{platform_key}'") from exc

    def prefix(self, platform_key: str) -> str:
        """Return canonical source-id prefix for a platform."""
        key = str(platform_key or '').strip().lower()
        plugin = self._plugins.maybe_get(key)
        return plugin.source_prefix if plugin else CONNECTOR_SOURCE_PREFIX.get(key, key.upper())

    def prefixed_source_id(self, platform_key: str, chat_id: str) -> str:
        """Build a prefixed source-id using platform prefix and chat id."""
        return f'{self.prefix(platform_key)}:{str(chat_id or "").strip()}'

    def all_prefixes(self) -> set[str]:
        """List all configured uppercase source-id prefixes."""
        return {plugin.source_prefix.upper() for plugin in self._plugins}

    async def close_all(self) -> None:
        """Close all unique connector runtimes and release resources."""
        visited: set[int] = set()
        for plugin in self._plugins:
            connector = plugin.connector
            marker = id(connector)
            if marker in visited:
                continue
            visited.add(marker)
            await connector.close()


connector_registry = ConnectorRegistry()

