"""Platform plugin descriptors and the built-in plugin registry."""

from wootify.plugins.base import PlatformPlugin
from wootify.plugins.registry import PlatformPluginRegistry

__all__ = ["PlatformPlugin", "PlatformPluginRegistry", "platform_plugin_registry"]


def __getattr__(name: str):
    """Load provider implementations only when the composition root asks."""
    if name == "platform_plugin_registry":
        from wootify.plugins.builtins import platform_plugin_registry

        return platform_plugin_registry
    raise AttributeError(name)
