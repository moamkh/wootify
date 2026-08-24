"""ASGI middleware enforcing panel JWT auth on panel endpoints only.

Protected: everything under ``/api/v1/`` **except** the public exclusions
below. The exclusions are deliberate:

* ``/api/v1/panel/auth/*``  — the login flow itself must be reachable.
* ``/api/v1/webhooks/*``    — inbound Chatwoot platform webhooks; Chatwoot
  cannot present a panel JWT, and these are the connector's public surface.

Everything outside ``/api/v1`` (``/health``, docs, static assets) is
untouched. When no panel password is configured the middleware is a no-op
(development mode).
"""

from __future__ import annotations

import json
import logging

from starlette.types import ASGIApp, Receive, Scope, Send

from app.panel_auth.service import panel_auth_service

logger = logging.getLogger("app.panel_auth.middleware")

_PROTECTED_PREFIX = "/api/v1"
_PUBLIC_PREFIXES = (
    "/api/v1/panel/auth",
    "/api/v1/webhooks",
)


class PanelAuthMiddleware:
    """Pure ASGI middleware (no BaseHTTPMiddleware) to avoid buffering costs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "").upper()

        if (
            method == "OPTIONS"  # CORS preflight must always pass
            or not path.startswith(_PROTECTED_PREFIX)
            or any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)
            or not panel_auth_service.enabled
        ):
            await self.app(scope, receive, send)
            return

        token = self._extract_bearer(scope)
        payload = panel_auth_service.verify_token(token) if token else None
        if payload is None:
            await self._reject(send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _extract_bearer(scope: Scope) -> str:
        """Pull the Bearer token from the Authorization header."""
        for name, value in scope.get("headers") or []:
            if name.lower() == b"authorization":
                text = value.decode("latin-1").strip()
                if text.lower().startswith("bearer "):
                    return text[7:].strip()
                return ""
        return ""

    @staticmethod
    async def _reject(send: Send) -> None:
        """Return a 401 JSON response."""
        body = json.dumps({"detail": "panel authentication required"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
