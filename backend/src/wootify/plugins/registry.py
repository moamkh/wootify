"""Object-oriented registry for platform plugin descriptors."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from wootify.plugins.base import PlatformPlugin


class PlatformPluginRegistry:
    """Own and resolve platform plugins by normalized public key."""

    def __init__(self, plugins: Iterable[PlatformPlugin] = ()) -> None:
        self._plugins: dict[str, PlatformPlugin] = {}
        for plugin in plugins:
            self.register(plugin)

    def register(self, plugin: PlatformPlugin) -> PlatformPlugin:
        """Register a plugin, rejecting accidental key shadowing."""
        if plugin.key in self._plugins:
            raise ValueError(f"Platform plugin '{plugin.key}' is already registered")
        self._plugins[plugin.key] = plugin
        return plugin

    def get(self, key: str) -> PlatformPlugin:
        normalized = str(key or "").strip().lower()
        plugin = self._plugins.get(normalized)
        if plugin is None:
            raise ValueError(f"Unsupported platform plugin '{key}'")
        return plugin

    def maybe_get(self, key: str) -> PlatformPlugin | None:
        return self._plugins.get(str(key or "").strip().lower())

    def keys(self) -> tuple[str, ...]:
        return tuple(self._plugins)

    def values(self) -> tuple[PlatformPlugin, ...]:
        return tuple(self._plugins.values())

    def __iter__(self) -> Iterator[PlatformPlugin]:
        return iter(self._plugins.values())

    def __len__(self) -> int:
        return len(self._plugins)
