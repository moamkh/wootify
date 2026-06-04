"""
Bale Messaging Client (bale.messaging.v2.Messaging)
====================================================

Reverse-engineered from web.bale.ai WebSocket frame captures.

Uses WebSocket transport with custom Y/Z protobuf framing.

Methods
-------
- SendMessage(peer_id, text, reply_to_message_id=None)
- UpdateMessage(peer_id, message_id, text) → edit message
- MessageRead(peer_id, max_id) → mark messages as read
- StopTyping(peer_id) → stop typing indicator
"""

import logging
from typing import Any, Dict, Optional

from .messaging_messages import (
    SendMessageRequest,
    UpdateMessageRequest,
    MessageReadRequest,
    StopTypingRequest,
)
from .ws_client import BaleWebSocketClient

logger = logging.getLogger("bale_grpc_client.messaging")


class BaleMessagingClient:
    """Client for Bale messaging service (bale.messaging.v2.Messaging)."""

    SERVICE = "bale.messaging.v2.Messaging"

    def __init__(
        self,
        jwt_token: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        update_queue: Optional[Any] = None,
    ):
        self.ws = BaleWebSocketClient(
            jwt_token=jwt_token,
            metadata=metadata,
            on_message=self._on_message,
            on_update=self._on_update,
            update_queue=update_queue,
        )
        self._last_message_handler: Optional[Any] = None

    def _on_message(self, data: bytes) -> None:
        logger.debug("Received message response: %s bytes", len(data))

    def _on_update(self, data: bytes) -> None:
        logger.debug("Received update: %s bytes", len(data))
        if self._last_message_handler:
            try:
                self._last_message_handler(data)
            except Exception:
                logger.exception("Update handler error")

    async def connect(self) -> None:
        """Connect WebSocket and perform handshake."""
        await self.ws.connect()

    async def close(self) -> None:
        """Close connection."""
        await self.ws.close()

    async def send_message(
        self,
        peer_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None,
    ) -> bytes:
        """Send a text message to a peer.

        Returns the raw protobuf response bytes.
        """
        req = SendMessageRequest(
            peer_id=peer_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
        )
        return await self.ws.send_request(
            service_name=self.SERVICE,
            method="SendMessage",
            payload=req.serialize(),
        )

    async def update_message(
        self,
        peer_id: int,
        message_id: int,
        text: str,
    ) -> bytes:
        """Edit an existing message.

        Returns the raw protobuf response bytes.
        """
        req = UpdateMessageRequest(
            peer_id=peer_id,
            message_id=message_id,
            text=text,
        )
        return await self.ws.send_request(
            service_name=self.SERVICE,
            method="UpdateMessage",
            payload=req.serialize(),
        )

    async def message_read(
        self,
        peer_id: int,
        max_id: int,
    ) -> bytes:
        """Mark messages as read up to max_id.

        Returns the raw protobuf response bytes.
        """
        req = MessageReadRequest(
            peer_id=peer_id,
            max_id=max_id,
        )
        return await self.ws.send_request(
            service_name=self.SERVICE,
            method="MessageRead",
            payload=req.serialize(),
        )

    async def stop_typing(self, peer_id: int) -> None:
        """Send stop typing indicator (fire-and-forget)."""
        req = StopTypingRequest(peer_id=peer_id)
        await self.ws.send_update(
            service_name=self.SERVICE,
            method="StopTyping",
            payload=req.serialize(),
        )
