"""
Module Overview
---------------
Purpose: Platform connector implementations and connector registry abstractions.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Any

from app.connectors.bale_connector import bale
from app.connectors.bale_pv_connector import bale_pv
from app.connectors.base_connector import PlatformConnector
from app.connectors.telegram_connector import telegram


CONNECTOR_SOURCE_PREFIX = {
    'bale': 'BALE',
    'bale_enterprise': 'BALE_ENTERPRISE',
    'bale_pv_enterprise': 'BALE_PV',
    'telegram': 'TELEGRAM',
    'telegram_enterprise': 'TELEGRAM_ENTERPRISE',
}


class ConnectorRegistry:
    """Represents connector registry."""
    def __init__(self) -> None:
        """Initialize the instance."""
        self._connectors: dict[str, PlatformConnector] = {
            'bale': bale,
            'bale_enterprise': bale,
            'bale_pv_enterprise': bale_pv,
            'telegram': telegram,
            'telegram_enterprise': telegram,
        }

    def get(self, platform_key: str) -> PlatformConnector:
        """Get connector implementation for a platform key."""
        key = str(platform_key or '').strip().lower()
        connector = self._connectors.get(key)
        if not connector:
            raise ValueError(f"Unsupported platform connector '{platform_key}'")
        return connector

    def prefix(self, platform_key: str) -> str:
        """Return canonical source-id prefix for a platform."""
        key = str(platform_key or '').strip().lower()
        return CONNECTOR_SOURCE_PREFIX.get(key, key.upper())

    def prefixed_source_id(self, platform_key: str, chat_id: str) -> str:
        """Build a prefixed source-id using platform prefix and chat id."""
        return f'{self.prefix(platform_key)}:{str(chat_id or "").strip()}'

    def all_prefixes(self) -> set[str]:
        """List all configured uppercase source-id prefixes."""
        return {value.upper() for value in CONNECTOR_SOURCE_PREFIX.values()}

    async def close_all(self) -> None:
        """Close all unique connector runtimes and release resources."""
        visited: set[int] = set()
        for connector in self._connectors.values():
            marker = id(connector)
            if marker in visited:
                continue
            visited.add(marker)
            await connector.close()


connector_registry = ConnectorRegistry()

