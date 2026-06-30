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
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import websockets

from .protobuf_wire import ProtobufMessage, ProtobufParser

logger = logging.getLogger("bale_pv_connector.ws")

# ---------------------------------------------------------------------------
# Optional raw-frame debug logger
# ---------------------------------------------------------------------------
# When the BALE_WS_DEBUG_LOG environment variable is set to a file path, every
# inbound WebSocket frame is appended to that file as hex + decoded text.
# This is disabled by default (empty string).  Enable temporarily to capture
# traffic for protocol reverse-engineering; never leave enabled in production
# as it generates hundreds of MB of output quickly.
_DEBUG_LOG_PATH = os.environ.get("BALE_WS_DEBUG_LOG", "")
_debug_file_handler: Optional[logging.FileHandler] = None


def _get_debug_logger() -> logging.Logger:
    """Return a file logger that records raw WS frames (hex + decoded).

    The handler is created lazily on first call and reused thereafter.
    If ``BALE_WS_DEBUG_LOG`` is unset or empty the returned logger has no
    handlers and all calls to it are no-ops.
    """
    global _debug_file_handler
    dbg = logging.getLogger("bale_pv_connector.ws.raw")
    if _debug_file_handler is None and _DEBUG_LOG_PATH:
        _debug_file_handler = logging.FileHandler(_DEBUG_LOG_PATH, mode="a", encoding="utf-8")
        _debug_file_handler.setFormatter(logging.Formatter("%(message)s"))
        dbg.addHandler(_debug_file_handler)
        dbg.setLevel(logging.DEBUG)
    return dbg


# Known field-name maps for the Bale Y/Z wrapper layers. These make the raw
# debug logs readable even without the official .proto files.
_Z_WRAPPER_NAMES: Dict[int, str] = {
    1: "response",
    2: "update",
    3: "terminate_session",
    4: "pong",
    5: "handshake_response",
}

_INNER_WRAPPER_NAMES: Dict[int, str] = {
    1: "payload",
    3: "index",
    4: "server_ts",
    5: "status",
}

_CONTAINER_NAMES: Dict[int, str] = {
    1: "event_wrapper",
    2: "container_seq_or_uid",
    3: "container_counter",
    4: "container_ts",
}

_EVENT_WRAPPER_NAMES: Dict[int, str] = {
    4: "status",
    5: "heartbeat",
    19: "message_status",
    21: "typing",
    46: "contact_status",
    50: "read_receipt",
    55: "new_message",
    131: "app_settings",
    162: "channel_message",
}

_UPDATE_MESSAGE_NAMES: Dict[int, str] = {
    1: "peer",
    2: "sender_uid",
    3: "date",
    4: "rid",
    5: "message",
    6: "reply_to_or_fwd",
    7: "forward_or_reply",
    8: "reply_to_or_fwd_alt",
    9: "sender_peer",
    14: "peer_info",
}

_MESSAGE_NAMES: Dict[int, str] = {
    4: "document",
    15: "text",
}

_TEXT_MESSAGE_NAMES: Dict[int, str] = {
    1: "text",
}

_DOCUMENT_MESSAGE_NAMES: Dict[int, str] = {
    1: "file_id",
    2: "access_hash",
    3: "file_size",
    4: "name",
    5: "mime_type",
    6: "thumb",
    7: "ext",
    8: "caption",
}

_PEER_NAMES: Dict[int, str] = {
    1: "type",
    2: "id",
    3: "access_hash",
}

# Common bale.users.v1.Users response schemas (used by LoadUsers/ImportContacts).
_USER_NAMES: Dict[int, str] = {
    1: "id",
    2: "access_hash",
    3: "name",
    4: "local_name",
    5: "sex",
    6: "avatar",
    7: "username_or_bot",
    9: "nick",
    16: "about_or_deleted",
    19: "created_at_or_last_seen",
    20: "ex_info",
}

_LOAD_USERS_RESPONSE_NAMES: Dict[int, str] = {
    1: "users",
}

# Generic Int64Value / StringValue / BoolValue wrappers.
_INT64_VALUE_NAMES: Dict[int, str] = {
    1: "value",
}

_EX_INFO_NAMES: Dict[int, str] = {
    1: "flags",
}


def _decode_protobuf_for_log(
    data: bytes,
    *,
    depth: int = 0,
    max_depth: int = 8,
    names: Optional[Dict[int, str]] = None,
) -> Any:
    """Recursively decode protobuf bytes into a human-readable structure.

    Length-delimited fields are tried as UTF-8 strings, then as nested
    protobuf messages, then left as hex. Varints and fixed fields are
    shown as integers / hex.

    When ``names`` is provided, field numbers are replaced with readable
    names in the output and the next nesting level gets a schema hint when
    we know it.
    """
    if depth >= max_depth:
        return data.hex()

    try:
        fields = ProtobufParser(data).parse()
    except Exception:
        return data.hex()

    result: Dict[str, List[Any]] = {}
    for field_number, values in fields.items():
        key = names.get(field_number, str(field_number)) if names else str(field_number)
        # Decide which schema to use for children of this field.
        child_names: Optional[Dict[int, str]] = None
        if names is _Z_WRAPPER_NAMES:
            if field_number == 1:  # response -> inner Response message
                child_names = {1: "error", 2: "response_payload", 3: "index"}
            elif field_number == 2:  # update -> inner wrapper
                child_names = _INNER_WRAPPER_NAMES
            else:
                child_names = None
        elif names is not None and set(names.keys()) == {1, 2, 3} and "response_payload" in names.values():
            # Inside the inner Response message: field 2 is the service payload.
            if field_number == 2:
                # Heuristic: LoadUsers/ImportContacts responses wrap repeated User in field 1.
                child_names = _LOAD_USERS_RESPONSE_NAMES
            elif field_number == 1:  # error
                child_names = {1: "code", 2: "message"}
        elif names is _INNER_WRAPPER_NAMES and field_number == 1:
            # payload may be a container or a raw event wrapper.
            child_names = _CONTAINER_NAMES
        elif names is _CONTAINER_NAMES and field_number == 1:
            child_names = _EVENT_WRAPPER_NAMES
        elif names is _EVENT_WRAPPER_NAMES and field_number in (55, 162):
            child_names = _UPDATE_MESSAGE_NAMES
        elif names is _UPDATE_MESSAGE_NAMES and field_number == 1:
            child_names = _PEER_NAMES
        elif names is _UPDATE_MESSAGE_NAMES and field_number == 5:
            child_names = _MESSAGE_NAMES
        elif names is _MESSAGE_NAMES and field_number == 15:
            child_names = _TEXT_MESSAGE_NAMES
        elif names is _MESSAGE_NAMES and field_number == 4:
            child_names = _DOCUMENT_MESSAGE_NAMES
        elif names is _LOAD_USERS_RESPONSE_NAMES and field_number == 1:
            child_names = _USER_NAMES
        elif names is _USER_NAMES and field_number == 19:
            child_names = _INT64_VALUE_NAMES
        elif names is _USER_NAMES and field_number == 20:
            child_names = _EX_INFO_NAMES

        decoded_values: List[Any] = []
        for value in values:
            if isinstance(value, bytes):
                # If we have a schema hint for this field, prefer protobuf.
                if child_names is not None:
                    try:
                        nested = _decode_protobuf_for_log(
                            value, depth=depth + 1, max_depth=max_depth, names=child_names
                        )
                        decoded_values.append(nested)
                        continue
                    except Exception:
                        pass
                # Otherwise try UTF-8 string first.
                try:
                    text = value.decode("utf-8")
                    decoded_values.append(text)
                    continue
                except UnicodeDecodeError:
                    pass
                # Try nested protobuf without a schema.
                try:
                    nested = _decode_protobuf_for_log(
                        value, depth=depth + 1, max_depth=max_depth, names=None
                    )
                    decoded_values.append(nested)
                    continue
                except Exception:
                    pass
                # Fall back to hex.
                decoded_values.append(value.hex())
            elif isinstance(value, int):
                decoded_values.append(value)
            else:
                decoded_values.append(str(value))
        result[key] = decoded_values
    return result


def _log_raw_frame(label: str, data: bytes) -> None:
    """Write a timestamped decoded dump of raw WS data to the debug log."""
    dbg = _get_debug_logger()
    ts = datetime.now(timezone.utc).isoformat()
    # Start decoding with the Z-wrapper schema so top-level fields get names.
    decoded = _decode_protobuf_for_log(data, names=_Z_WRAPPER_NAMES)
    try:
        decoded_str = json.dumps(decoded, ensure_ascii=False, indent=2)
    except Exception:
        decoded_str = repr(decoded)
    # Truncate if absurdly large (>64KB) to keep log usable
    if len(decoded_str) > 131_072:
        decoded_str = decoded_str[:131_072] + f"...[truncated {len(data)} bytes total]"
    dbg.debug("[%s] %s | len=%d | decoded=%s", ts, label, len(data), decoded_str)


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
    error: Optional[Dict[str, Any]] = None

    @classmethod
    def parse(cls, data: bytes) -> "WsResponse":
        parser = ProtobufParser(data)
        fields = parser.parse()
        result = cls()

        # Field 1: response (inner Response message)
        # Inner Response layout: 1=error, 2=response_payload, 3=index
        if 1 in fields:
            inner_response = fields[1][0]
            if isinstance(inner_response, bytes):
                p2 = ProtobufParser(inner_response)
                f2 = p2.parse()
                result.index = f2.get(3, [None])[0]
                # Error field (1) takes precedence over response payload.
                if 1 in f2:
                    error_bytes = f2[1][0]
                    if isinstance(error_bytes, bytes):
                        pe = ProtobufParser(error_bytes)
                        fe = pe.parse()
                        result.error = {
                            "code": fe.get(1, [None])[0],
                            "message": (
                                fe.get(2, [b""])[0].decode("utf-8", errors="replace")
                                if isinstance(fe.get(2, [b""])[0], bytes)
                                else None
                            ),
                        }
                else:
                    result.response = f2.get(2, [None])[0]

        # Field 2: update (inner Update message wrapper).
        # update_parser.parse_ws_update expects this wrapper (fields 1, 3, 4).
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
                    _log_raw_frame("WS_RECV", msg)
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
            _log_raw_frame("HANDSHAKE", data)
            logger.debug("Received handshake response")
            return

        if resp.pong:
            _log_raw_frame("PONG", data)
            logger.debug("Received pong")
            return

        if resp.terminate_session:
            _log_raw_frame("TERMINATE", data)
            logger.warning("Received terminateSession")
            self._connected = False
            return

        if resp.update is not None:
            _log_raw_frame("UPDATE", data)
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

        if resp.response is not None or resp.error is not None:
            _log_raw_frame("RESPONSE", data)
            # Resolve pending response
            if resp.index is not None and resp.index in self._pending_responses:
                future = self._pending_responses.pop(resp.index)
                if not future.done():
                    if resp.error is not None:
                        from .exceptions import BaleRpcError
                        future.set_exception(
                            BaleRpcError(
                                f"{resp.error.get('code')}: {resp.error.get('message')}",
                                code=resp.error.get("code"),
                            )
                        )
                    else:
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
        """Close the WebSocket connection and cancel all pending requests.

        Safe to call multiple times; subsequent calls are no-ops.
        """
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
        # Cancel any in-flight response futures so callers don't hang.
        for future in self._pending_responses.values():
            if not future.done():
                future.cancel()
        self._pending_responses.clear()
