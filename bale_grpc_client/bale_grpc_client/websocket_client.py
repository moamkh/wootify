"""
Bale WebSocket Client
=====================
Handles real-time message receiving and session management.

Reverse-engineered from web.bale.ai JS bundle.

WebSocket Protocol
------------------
Endpoint: wss://next-ws.bale.ai (primary) or wss://maviz-ws.bale.ai
Binary type: arraybuffer

Message Format (protobuf)
-------------------------
Server → Client:
  - response: RPC response wrapper
  - update: Real-time push (new messages, notifications, etc.)
  - terminateSession: Force logout signal

Client → Server:
  - Binary protobuf frames containing RPC requests

Connection Flow
---------------
1. Open WebSocket to wss://next-ws.bale.ai
2. Send authentication/handshake (exact format TBD)
3. Server starts pushing updates
4. Handle ping/pong to keep connection alive
5. Reconnect with exponential backoff on disconnect

Usage Example
-------------
    ws = BaleWebSocketClient(jwt_token="...")
    await ws.connect()

    async for update in ws.updates():
        if update.get("message"):
            print(f"New message: {update['message']}")
"""

import asyncio
import logging
import struct
from typing import Any, AsyncIterator, Callable, Dict, Optional

import websockets

from .exceptions import BaleConnectionError

logger = logging.getLogger("bale_grpc_client.ws")

WS_ENDPOINT = "wss://next-ws.bale.ai"
WS_FALLBACK = "wss://maviz-ws.bale.ai"


class BaleWebSocketClient:
    """WebSocket client for real-time Bale updates."""

    def __init__(
        self,
        jwt_token: str,
        endpoint: str = WS_ENDPOINT,
        on_message: Optional[Callable[[Dict], None]] = None,
        on_terminate: Optional[Callable[[], None]] = None,
    ):
        self.jwt_token = jwt_token
        self.endpoint = endpoint
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._stop_event = asyncio.Event()
        self._on_message = on_message
        self._on_terminate = on_terminate
        self._reconnect_count = 0
        self._base_reconnect_timeout = 1.0
        self._max_reconnect_timeout = 60.0

    async def connect(self) -> None:
        """Establish WebSocket connection."""
        try:
            self.ws = await websockets.connect(
                self.endpoint,
                extra_headers={
                    "Origin": "https://web.bale.ai",
                    # Additional headers may be needed
                },
            )
            self._reconnect_count = 0
            logger.info("WebSocket connected to %s", self.endpoint)
            asyncio.create_task(self._receive_loop())
        except Exception as exc:
            raise BaleConnectionError(f"WebSocket connection failed: {exc}") from exc

    async def _receive_loop(self) -> None:
        """Main receive loop."""
        try:
            while not self._stop_event.is_set():
                try:
                    message = await self.ws.recv()
                    await self._handle_message(message)
                except websockets.exceptions.ConnectionClosed:
                    logger.warning("WebSocket closed, reconnecting...")
                    await self._reconnect()
                    break
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("WebSocket receive error: %s", exc)

    async def _handle_message(self, data: bytes) -> None:
        """Parse and handle WebSocket message."""
        try:
            # The server sends Uint8Array protobuf messages
            # Format: server pack with response/update/terminateSession fields
            if isinstance(data, str):
                logger.debug("Received text: %s", data)
                return

            # Parse protobuf server pack
            # NOTE: Need protobuf definitions for ServerPack message
            logger.debug("Received binary message: %d bytes", len(data))

            # Placeholder: In real implementation, decode protobuf
            # pack = ServerPack.FromString(data)
            # if pack.terminateSession:
            #     await self._handle_terminate()
            # elif pack.update:
            #     await self._handle_update(pack.update)
            # elif pack.response:
            #     await self._handle_response(pack.response)

        except Exception as exc:
            logger.error("Failed to handle message: %s", exc)

    async def _handle_terminate(self) -> None:
        """Handle session termination signal."""
        logger.warning("Server requested session termination")
        if self._on_terminate:
            self._on_terminate()
        await self.close()

    async def _reconnect(self) -> None:
        """Reconnect with exponential backoff."""
        if self._stop_event.is_set():
            return

        timeout = min(
            self._base_reconnect_timeout * (2 ** self._reconnect_count),
            self._max_reconnect_timeout,
        )
        self._reconnect_count += 1
        logger.info("Reconnecting in %.1f seconds...", timeout)
        await asyncio.sleep(timeout)
        await self.connect()

    async def close(self) -> None:
        """Close WebSocket connection."""
        self._stop_event.set()
        if self.ws:
            await self.ws.close()
            self.ws = None
        logger.info("WebSocket closed")
