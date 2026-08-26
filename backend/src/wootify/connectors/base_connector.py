"""
Module Overview
---------------
Purpose: Platform connector protocol definitions used by service and bridge layers.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class PlatformConnector(Protocol):
    """Protocol contract implemented by all platform connectors."""

    async def connect(self, instance: str, params: dict[str, Any], proxy: Optional[dict[str, Any]] = None) -> None:
        """Initialize or refresh connector runtime for a specific instance."""

    async def disconnect(self, instance: str) -> None:
        """Stop connector runtime for a specific instance."""

    async def send_text(
        self,
        instance: str,
        chat_id: str,
        text: str,
        quoted: Optional[dict[str, Any]] = None,
        reply_markup: Any = None,
    ) -> dict[str, Any]:
        """Send a text message to the target platform chat."""

    async def send_media(
        self,
        instance: str,
        chat_id: str,
        media_url_or_bytes: Any,
        filename: str,
        caption: Optional[str] = None,
        quoted: Optional[dict[str, Any]] = None,
        reply_markup: Any = None,
    ) -> dict[str, Any]:
        """Send media content to the target platform chat."""

    async def update_message(
        self,
        instance: str,
        chat_id: str,
        message_id: str,
        text: str,
    ) -> dict[str, Any]:
        """Edit an existing message on the target platform."""

    async def delete_message(
        self,
        instance: str,
        chat_id: str,
        message_id: str,
    ) -> dict[str, Any]:
        """Delete a message on the target platform."""

    async def get_updates(
        self,
        instance: str,
        offset: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> dict[str, Any]:
        """Fetch inbound platform updates for polling workflows."""

    async def download_file_by_id(
        self,
        instance: str,
        file_id: str,
    ) -> tuple[bytes, Optional[str], Optional[str]]:
        """Download a platform file payload by its provider-specific file ID."""

    async def get_connection_state(self, instance: str) -> dict[str, Any]:
        """Return connection health state for a specific instance.

        Returns a dict with at least ``connected`` (bool) and ``detail`` (str).
        """

    async def close(self) -> None:
        """Release connector resources for all tracked instances."""
