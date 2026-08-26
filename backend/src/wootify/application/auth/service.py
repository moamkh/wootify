"""Panel authentication service.

Verifies panel credentials from environment settings and issues/verifies
HS256 JWTs. When ``PANEL_AUTH_PASSWORD`` is empty the panel runs in
*auth-disabled* mode (development default) and the middleware lets all
requests through; set the password in ``.env`` to enforce login.

Includes a small in-memory login rate limiter (per client IP) to blunt
credential brute-forcing.
"""

from __future__ import annotations

import hmac
import logging
import time
from typing import Any, Optional

from wootify.config import settings
from wootify.infrastructure.security import jwt as jwt_utils

logger = logging.getLogger("app.panel_auth.service")

#: Login brute-force protection: max failures per IP before a lockout window.
_MAX_LOGIN_FAILURES = 5
_LOCKOUT_SECONDS = 60.0


class PanelAuthService:
    """Issue and verify panel JWTs."""

    def __init__(self) -> None:
        # client_ip -> (failure_count, locked_until_monotonic)
        self._login_failures: dict[str, tuple[int, float]] = {}

    @property
    def enabled(self) -> bool:
        """Auth is enforced only when a panel password is configured."""
        return bool(str(settings.PANEL_AUTH_PASSWORD or "").strip())

    def _secret(self) -> str:
        """JWT signing secret; falls back to the data-encryption key."""
        explicit = str(settings.PANEL_AUTH_JWT_SECRET or "").strip()
        if explicit:
            return explicit
        derived = str(settings.DATA_ENCRYPTION_KEY or "").strip()
        if derived:
            return derived
        # Last resort: a fixed dev secret (auth still requires a password).
        return "wootify-panel-dev-secret"

    def is_locked_out(self, client_ip: str) -> bool:
        """Return True while a client IP sits in the brute-force lockout window."""
        _, locked_until = self._login_failures.get(client_ip, (0, 0.0))
        return time.monotonic() < locked_until

    def verify_credentials(self, username: str, password: str, client_ip: str = "") -> bool:
        """Constant-time credential check with per-IP failure lockout."""
        if not self.enabled:
            return True
        if client_ip and self.is_locked_out(client_ip):
            logger.warning("panel login locked_out ip=%s", client_ip)
            return False

        expected_user = str(settings.PANEL_AUTH_USERNAME or "admin")
        expected_pass = str(settings.PANEL_AUTH_PASSWORD or "")
        ok = hmac.compare_digest(str(username), expected_user) and hmac.compare_digest(
            str(password), expected_pass
        )

        if client_ip:
            if ok:
                self._login_failures.pop(client_ip, None)
            else:
                failures, _ = self._login_failures.get(client_ip, (0, 0.0))
                failures += 1
                locked_until = time.monotonic() + _LOCKOUT_SECONDS if failures >= _MAX_LOGIN_FAILURES else 0.0
                self._login_failures[client_ip] = (failures, locked_until)
                logger.warning("panel login failed ip=%s failures=%s", client_ip, failures)
        return ok

    def issue_token(self, username: str) -> str:
        """Issue a signed panel JWT for an authenticated username."""
        return jwt_utils.encode(
            {"sub": str(username), "scope": "panel"},
            self._secret(),
            ttl_seconds=int(settings.PANEL_AUTH_TOKEN_TTL_MINUTES) * 60,
        )

    def verify_token(self, token: str) -> Optional[dict[str, Any]]:
        """Return the token payload when valid, otherwise None."""
        try:
            payload = jwt_utils.decode(token, self._secret())
        except jwt_utils.JwtError:
            return None
        if payload.get("scope") != "panel":
            return None
        return payload


panel_auth_service = PanelAuthService()
