"""Characterization tests for the provider plugin boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from wootify.connectors.registry import connector_registry
from wootify.domain.platform import PlatformKey
from wootify.plugins.base import PlatformPlugin
from wootify.plugins.builtins import platform_plugin_registry
from wootify.plugins.registry import PlatformPluginRegistry


EXPECTED_KEYS = {
    "bale",
    "bale_enterprise",
    "bale_pv_enterprise",
    "instagram_pv_enterprise",
    "telegram",
    "telegram_enterprise",
}


def test_builtin_registry_preserves_public_platform_keys() -> None:
    assert set(platform_plugin_registry.keys()) == EXPECTED_KEYS
    assert connector_registry.prefix("BALE_ENTERPRISE") == "BALE_ENTERPRISE"
    for key in EXPECTED_KEYS:
        assert connector_registry.get(key) is platform_plugin_registry.get(key).connector


def test_plugins_expose_capabilities_and_experimental_flag() -> None:
    assert platform_plugin_registry.get("bale").supports("send_text")
    assert not platform_plugin_registry.get("bale_enterprise").supports("reply_sync")
    assert platform_plugin_registry.get("instagram_pv_enterprise").experimental is True
    assert platform_plugin_registry.get("bale_pv_enterprise").adapter_factory is not None


def test_registry_normalizes_keys_and_rejects_duplicates() -> None:
    plugin = PlatformPlugin(" Custom_Key ", "Custom", object(), "CUSTOM")
    registry = PlatformPluginRegistry([plugin])
    assert registry.get("custom_key") is plugin
    with pytest.raises(ValueError, match="already registered"):
        registry.register(PlatformPlugin("custom_key", "Other", object(), "OTHER"))


def test_platform_key_is_a_small_domain_value_object() -> None:
    assert str(PlatformKey("  TELEGRAM_ENTERPRISE ")) == "telegram_enterprise"
    with pytest.raises(ValueError, match="cannot be empty"):
        PlatformKey(" ")


def test_domain_has_no_provider_or_web_imports() -> None:
    domain_root = Path(__file__).parents[1] / "src" / "wootify" / "domain"
    forbidden = ("wootify.adapters", "wootify.clients", "wootify.connectors", "wootify.services", "fastapi", "sqlalchemy")
    for path in domain_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert not any(name.startswith(forbidden) for name in imports), (path, imports)
