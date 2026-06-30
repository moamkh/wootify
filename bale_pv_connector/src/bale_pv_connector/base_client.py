"""
Base gRPC-Web client for Bale Messenger.

Reverse-engineered from web.bale.ai static JS bundle analysis.
"""

import asyncio
import json
import logging
import struct
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("bale_pv_connector")

# Endpoints discovered by analyzing network traffic from web.bale.ai
GRPC_HOST = "https://next-ws.bale.ai"
WS_ENDPOINT_PRIMARY = "wss://next-ws.bale.ai"
WS_ENDPOINT_FALLBACK = "wss://maviz-ws.bale.ai"

# Custom headers required by Bale's servers
DEFAULT_METADATA = {
    "mt_app_version": "157595",
    "app_version": "157595",
    "browser_type": "1",
    "mt_browser_type": "1",
    "browser_version": "148.0.0.0",
    "mt_browser_version": "148.0.0.0",
    "os_type": "3",
    "mt_os_type": "3",
    "x-grpc-web": "1",
}


@dataclass
class RpcMethod:
    """Represents a gRPC-Web method definition."""

    service_name: str
    method_name: str
    request_stream: bool = False
    response_stream: bool = False

    @property
    def path(self) -> str:
        return f"/{self.service_name}/{self.method_name}"


class BaleBaseClient:
    """Base client handling HTTP transport and common headers."""

    def __init__(
        self,
        host: str = GRPC_HOST,
        metadata: Optional[Dict[str, str]] = None,
        session_id: Optional[str] = None,
    ):
        self.host = host.rstrip("/")
        self.metadata = {**DEFAULT_METADATA, **(metadata or {})}
        self.session_id = session_id or str(int(time.time() * 1000))
        self.metadata["session_id"] = self.session_id
        self.metadata["mt_session_id"] = self.session_id
        self._client = httpx.AsyncClient(
            headers={
                "content-type": "application/grpc-web+proto",
                "x-grpc-web": "1",
            },
            timeout=30.0,
        )
        self._jwt_token: Optional[str] = None

    def set_jwt_token(self, token: str) -> None:
        """Set JWT token for authenticated requests."""
        self._jwt_token = token

    def _build_headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Build request headers with metadata and optional auth."""
        headers = {
            "content-type": "application/grpc-web+proto",
            "x-grpc-web": "1",
            **self.metadata,
        }
        if self._jwt_token:
            headers["authorization"] = f"Bearer {self._jwt_token}"
        if extra:
            headers.update(extra)
        return headers

    async def unary_call(
        self,
        method: RpcMethod,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Make a gRPC-Web unary call.

        NOTE: This is a stub implementation. Full protobuf serialization
        is required for production use. The payload should be serialized
        using the appropriate protobuf message definitions.
        """
        url = f"{self.host}{method.path}"
        headers = self._build_headers()

        # gRPC-Web framing:
        # 1 byte flags + 4 bytes length + protobuf payload
        # Flag 0x00 = uncompressed, 0x01 = compressed
        # For JSON fallback during development, some endpoints accept JSON
        body = self._serialize_grpc_web(payload)

        logger.debug("RPC %s -> %s", method.path, payload)
        response = await self._client.post(url, headers=headers, content=body)

        if response.status_code != 200:
            raise Exception(f"RPC failed: {response.status_code} {response.text}")

        return self._parse_grpc_web_response(response.content)

    def _serialize_grpc_web(self, payload: Dict[str, Any]) -> bytes:
        """
        Serialize payload to gRPC-Web frame.

        WARNING: This is a placeholder. Real implementation needs protobuf
        message definitions for each RPC method.
        """
        # For now, return empty frame as placeholder
        # Real impl would: protobuf_encode(payload) -> frame[0x00 + len(4 bytes) + bytes]
        import json

        json_bytes = json.dumps(payload).encode()
        # gRPC-Web frame: flags(1) + length(4) + payload
        return b"\x00" + struct.pack(">I", len(json_bytes)) + json_bytes

    def _parse_grpc_web_response(self, data: bytes) -> Dict[str, Any]:
        """
        Parse gRPC-Web response frame.

        WARNING: Placeholder implementation.
        """
        if len(data) < 5:
            return {}
        flags = data[0]
        length = struct.unpack(">I", data[1:5])[0]
        payload = data[5 : 5 + length]
        try:
            return json.loads(payload)
        except Exception:
            return {"raw": payload.hex(), "flags": flags}

    async def close(self) -> None:
        await self._client.aclose()
