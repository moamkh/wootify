"""Base platform adapter interface.

Mirrors the messenger_chatwoot_connector adapter pattern so each platform
provider can be plugged into the same Chatwoot bridge logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, Optional


class BasePlatformAdapter(ABC):
    """Abstract adapter for a messaging platform.

    Implementations hide platform-specific details (auth, polling, sending)
    and produce normalized events that the Chatwoot bridge can consume.
    """

    def __init__(self, instance_key: str, config: Dict[str, Any]) -> None:
        self.instance_key = instance_key
        self.config = config

    @abstractmethod
    async def connect(self) -> None:
        """Authenticate/connect the instance."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect the instance."""
        ...

    @abstractmethod
    async def send_text(
        self,
        peer_id: str,
        text: str,
        *,
        reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send a text message to a peer."""
        ...

    @abstractmethod
    async def send_media(
        self,
        peer_id: str,
        media: Any,
        *,
        filename: Optional[str] = None,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send media to a peer.

        ``media`` may be a URL string or bytes.
        """
        ...

    @abstractmethod
    async def poll_events(self) -> AsyncIterator[Dict[str, Any]]:
        """Yield normalized events from the platform.

        Each event must contain at least:
          - chat_id: str
          - chat_type: "private" | "group" | "channel"
          - from_name: str | None
          - text: str
          - message_id: str | None
          - platform_message_id: str | None
          - attachments: list[dict] | None
          - contact: dict | None
          - reply_to: dict | None
        """
        ...

    @abstractmethod
    def normalize_incoming_update(self, raw_update: Any) -> Optional[Dict[str, Any]]:
        """Convert a raw platform update into a normalized event."""
        ...

    @abstractmethod
    def get_self_id(self) -> Optional[str]:
        """Return the authenticated user's platform id, if known."""
        ...
