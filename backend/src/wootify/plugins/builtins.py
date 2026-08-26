"""Composition of existing connectors and adapters into built-in plugins."""

from __future__ import annotations

from wootify.adapters.bale_pv import BalePvAdapter
from wootify.instagram.adapter import InstagramPvAdapter
from wootify.connectors.bale_connector import bale
from wootify.connectors.bale_pv_connector import bale_pv
from wootify.connectors.telegram_connector import telegram
from wootify.plugins.base import PlatformPlugin
from wootify.plugins.registry import PlatformPluginRegistry


_BALE = {"send_text": True, "send_media": True, "reply_sync": True, "inbound_polling": True, "mark_as_read": False}
_BALE_ENTERPRISE = {"send_text": True, "send_media": True, "reply_sync": False, "inbound_polling": True, "mark_as_read": False}
_BALE_PV = {"send_text": True, "send_media": True, "reply_sync": True, "inbound_polling": True, "mark_as_read": False}
_TELEGRAM = dict(_BALE)
_TELEGRAM_ENTERPRISE = dict(_BALE_ENTERPRISE)


def _adapter(cls: type) -> object:
    return cls


def build_builtin_registry() -> PlatformPluginRegistry:
    """Build the registry used by the process composition root."""
    return PlatformPluginRegistry([
        PlatformPlugin("bale", "Bale", bale, "BALE", _BALE, family="bale"),
        PlatformPlugin("bale_enterprise", "Bale Enterprise", bale, "BALE_ENTERPRISE", _BALE_ENTERPRISE, family="bale"),
        PlatformPlugin("bale_pv_enterprise", "Bale PV (Personal)", bale_pv, "BALE_PV", _BALE_PV, adapter_factory=_adapter(BalePvAdapter), family="bale_pv"),
        PlatformPlugin("instagram_pv_enterprise", "Instagram PV (Personal)", _instagram_connector(), "INSTAGRAM_PV", _BALE_PV, adapter_factory=_adapter(InstagramPvAdapter), experimental=True, family="instagram"),
        PlatformPlugin("telegram", "Telegram", telegram, "TELEGRAM", _TELEGRAM, family="telegram"),
        PlatformPlugin("telegram_enterprise", "Telegram Enterprise", telegram, "TELEGRAM_ENTERPRISE", _TELEGRAM_ENTERPRISE, family="telegram"),
    ])


def _instagram_connector():
    from wootify.instagram.connector import instagram_pv

    return instagram_pv


platform_plugin_registry = build_builtin_registry()
