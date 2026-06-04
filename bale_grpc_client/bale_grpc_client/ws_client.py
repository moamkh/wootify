"""
Bale WebSocket Client for gRPC-Web over WebSocket
===================================================

Reverse-engineered from web.bale.ai JS bundle and live frame captures.

Protocol Overview
-----------------
1. Connect WebSocket to wss://next-ws.bale.ai/ws/
2. Send Y handshake message:
   Y { handshakeRequest { mkprotoVersion: 1, apiVersion: 1 } }
3. Wait for handshake response
4. Send Y request messages:
   Y { request { index, serviceName, method, metadata, payload } }
5. Receive Z response messages:
   Z { response | update | terminateSession | pong | handshakeResponse }

Auth
----
The server requires session authentication. Based on JS analysis, the web client:
1. Authenticates via HTTP gRPC-Web (StartPhoneAuth → ValidateCode)
2. Calls /set-cookie/ endpoint with JWT to establish session cookie
3. Opens WebSocket (browser sends cookie automatically)
4. Sends handshake → receives handshakeResponse → connected

In Python, we must manually manage cookies and pass them in the WebSocket handshake.

Note: As of testing, the JWT obtained earlier appears invalidated by the server.
Re-authentication may be required for WebSocket connectivity.
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import websockets

from .protobuf_wire import ProtobufMessage, ProtobufParser

logger = logging.getLogger("bale_grpc_client.ws")


# --- Protobuf Message Builders ---

class HandshakeRequest:
    """WebSocket handshake request."""

    def __init__(self, api_version: int = 1, mkproto_version: int = 1):
        self.api_version = api_version
        self.mkproto_version = mkproto_version

    def serialize(self) -> bytes:
        req = ProtobufMessage()
        req.add_int32(1, self.mkproto_version)
        req.add_int64(2, self.api_version)
        y = ProtobufMessage()
        y.add_message(3, req)
        return y.serialize()


class WsRequest:
    """Inner request message for Y wrapper."""

    def __init__(
        self,
        service_name: str,
        method: str,
        payload: bytes,
        metadata: Optional[Dict[str, str]] = None,
        index: Optional[int] = None,
    ):
        self.service_name = service_name
        self.method = method
        self.payload = payload
        self.metadata = metadata or {}
        self.index = index or random.randint(1, 2**63 - 1)

    def serialize(self) -> bytes:
        req = ProtobufMessage()
        req.add_string(1, self.service_name)
        req.add_string(2, self.method)
        req.add_bytes(3, self.payload)
        # Metadata
        meta_msg = ProtobufMessage()
        for key, value in self.metadata.items():
            entry = ProtobufMessage()
            entry.add_string(1, key)
            sv = ProtobufMessage()
            sv.add_string(1, value)
            entry.add_message(2, sv)
            meta_msg.add_message(1, entry)
        req.add_message(4, meta_msg)
        req.add_int64(5, self.index)
        y = ProtobufMessage()
        y.add_message(1, req)
        return y.serialize()


class WsPing:
    """Ping message for keepalive."""

    def serialize(self) -> bytes:
        y = ProtobufMessage()
        y.add_message(2, ProtobufMessage())
        return y.serialize()


@dataclass
class WsResponse:
    """Parsed WebSocket response."""

    response: Optional[bytes] = None
    update: Optional[bytes] = None
    terminate_session: Optional[bytes] = None
    pong: Optional[bytes] = None
    handshake_response: Optional[bytes] = None
    index: Optional[int] = None

    @classmethod
    def parse(cls, data: bytes) -> "WsResponse":
        parser = ProtobufParser(data)
        fields = parser.parse()
        result = cls()

        # Field 1: response (bytes)
        if 1 in fields:
            result.response = fields[1][0]
            # Parse inner response structure
            p2 = ProtobufParser(result.response)
            f2 = p2.parse()
            result.index = f2.get(3, [None])[0]  # index field in response

        # Field 2: update (bytes)
        if 2 in fields:
            result.update = fields[2][0]

        # Field 3: terminateSession (bytes)
        if 3 in fields:
            result.terminate_session = fields[3][0]

        # Field 4: pong (bytes)
        if 4 in fields:
            result.pong = fields[4][0]

        # Field 5: handshakeResponse (bytes)
        if 5 in fields:
            result.handshake_response = fields[5][0]

        return result


# --- WebSocket Client ---

class BaleWebSocketClient:
    """WebSocket client for Bale messaging service."""

    WS_URI = "wss://next-ws.bale.ai/ws/"
    ORIGIN = "https://web.bale.ai"

    def __init__(
        self,
        jwt_token: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        on_message: Optional[Callable[[bytes], None]] = None,
        on_update: Optional[Callable[[bytes], None]] = None,
        on_disconnect: Optional[Callable[[], None]] = None,
        update_queue: Optional[asyncio.Queue] = None,
    ):
        self.jwt_token = jwt_token
        self.metadata = metadata or {}
        self.on_message = on_message
        self.on_update = on_update
        self.on_disconnect = on_disconnect
        self.update_queue = update_queue
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._listen_task: Optional[asyncio.Task] = None
        self._req_index = 0
        self._pending_responses: Dict[int, asyncio.Future] = {}

    def _next_index(self) -> int:
        self._req_index += 1
        return self._req_index

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
            ),
        }
        if self.jwt_token:
            # The server requires the access_token cookie from /set-cookie/
            # We store it as a property after calling set_cookie
            if hasattr(self, '_access_token_cookie'):
                headers["Cookie"] = self._access_token_cookie
            else:
                # Fallback: try direct JWT (won't work for WS but kept for compat)
                headers["Cookie"] = f"bale_auth_token={self.jwt_token}"
        return headers

    async def _fetch_access_token_cookie(self) -> str:
        """Call /set-cookie/ to get the access_token cookie required for WS."""
        import httpx
        resp = await httpx.AsyncClient().post(
            "https://next-ws.bale.ai/set-cookie/",
            headers={
                "Authorization": f"Bearer {self.jwt_token}",
                "Origin": self.ORIGIN,
            },
        )
        if resp.status_code != 200:
            raise ConnectionError(f"set-cookie failed: {resp.status_code}")
        cookies = resp.cookies
        cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()])
        return cookie_str

    async def connect(self) -> None:
        """Establish WebSocket connection and perform handshake."""
        logger.info("Connecting to %s", self.WS_URI)

        # Fetch access_token cookie first if we have a JWT
        if self.jwt_token and not hasattr(self, '_access_token_cookie'):
            self._access_token_cookie = await self._fetch_access_token_cookie()
            logger.info("Got access_token cookie from /set-cookie/")

        self._ws = await websockets.connect(
            self.WS_URI,
            origin=self.ORIGIN,
            additional_headers=self._build_headers(),
        )

        # Send handshake
        handshake = HandshakeRequest()
        await self._ws.send(handshake.serialize())
        logger.debug("Handshake sent")

        # Wait for handshake response with timeout
        try:
            msg = await asyncio.wait_for(self._ws.recv(), timeout=10.0)
        except asyncio.TimeoutError:
            await self._ws.close()
            raise ConnectionError("Handshake timeout")

        resp = WsResponse.parse(msg)
        if resp.handshake_response is None:
            await self._ws.close()
            raise ConnectionError("No handshake response received")

        logger.info("WebSocket handshake successful")
        self._connected = True

        # Start listener
        self._listen_task = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        """Background task to receive messages."""
        try:
            while self._connected and self._ws:
                msg = await self._ws.recv()
                if isinstance(msg, bytes):
                    await self._handle_message(msg)
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("WebSocket closed: code=%s reason=%s", e.code, e.reason)
            self._connected = False
            if self.on_disconnect:
                try:
                    self.on_disconnect()
                except Exception:
                    pass
        except Exception as e:
            logger.error("Listen error: %s", e)
            self._connected = False

    async def _handle_message(self, data: bytes) -> None:
        """Handle incoming WebSocket message."""
        resp = WsResponse.parse(data)

        if resp.handshake_response:
            logger.debug("Received handshake response")
            return

        if resp.pong:
            logger.debug("Received pong")
            return

        if resp.terminate_session:
            logger.warning("Received terminateSession")
            self._connected = False
            return

        if resp.update:
            if self.update_queue is not None:
                try:
                    self.update_queue.put_nowait(resp.update)
                except Exception:
                    pass
            if self.on_update:
                try:
                    self.on_update(resp.update)
                except Exception:
                    logger.exception("on_update callback error")

        if resp.response:
            # Resolve pending response
            if resp.index is not None and resp.index in self._pending_responses:
                future = self._pending_responses.pop(resp.index)
                if not future.done():
                    future.set_result(resp.response)
            elif self.on_message:
                try:
                    self.on_message(resp.response)
                except Exception:
                    logger.exception("on_message callback error")

    async def send_request(
        self,
        service_name: str,
        method: str,
        payload: bytes,
        timeout: float = 30.0,
    ) -> bytes:
        """Send a unary request and wait for response."""
        if not self._connected or not self._ws:
            raise ConnectionError("WebSocket not connected")

        index = self._next_index()
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_responses[index] = future

        req = WsRequest(
            service_name=service_name,
            method=method,
            payload=payload,
            metadata=self.metadata,
            index=index,
        )

        await self._ws.send(req.serialize())

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending_responses.pop(index, None)
            raise TimeoutError(f"Request timeout for {service_name}/{method}")

    async def send_update(
        self,
        service_name: str,
        method: str,
        payload: bytes,
    ) -> None:
        """Send a request without waiting for response."""
        if not self._connected or not self._ws:
            raise ConnectionError("WebSocket not connected")

        index = self._next_index()
        req = WsRequest(
            service_name=service_name,
            method=method,
            payload=payload,
            metadata=self.metadata,
            index=index,
        )
        await self._ws.send(req.serialize())

    @property
    def is_connected(self) -> bool:
        """Return whether the WebSocket is connected and handshake completed."""
        if not self._connected or self._ws is None:
            return False
        # Handle both legacy WebSocketClientProtocol (has .open)
        # and modern ClientConnection (has .state enum)
        try:
            return self._ws.open
        except AttributeError:
            try:
                from websockets.protocol import State
                return self._ws.state == State.OPEN
            except Exception:
                return False

    async def close(self) -> None:
        """Close WebSocket connection."""
        self._connected = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()
            self._ws = None
        # Cancel any pending futures
        for future in self._pending_responses.values():
            if not future.done():
                future.cancel()
        self._pending_responses.clear()
