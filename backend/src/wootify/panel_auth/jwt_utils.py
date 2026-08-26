"""Minimal HS256 JSON Web Token implementation (no external dependency).

Only what the panel auth flow needs: ``encode`` a payload with ``exp`` and
``decode`` with signature + expiry verification. Uses HMAC-SHA256 from the
standard library so no JWT package is required.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Optional


class JwtError(Exception):
    """Base error for token problems."""


class JwtExpiredError(JwtError):
    """Token signature is valid but it has expired."""


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + padding)


def encode(payload: dict[str, Any], secret: str, ttl_seconds: int) -> str:
    """Encode a payload into a signed HS256 JWT with an ``exp`` claim."""
    header = {"alg": "HS256", "typ": "JWT"}
    body = dict(payload)
    body["iat"] = int(time.time())
    body["exp"] = int(time.time()) + int(ttl_seconds)
    signing_input = f"{_b64url_encode(json.dumps(header, separators=(',', ':')).encode())}." f"{_b64url_encode(json.dumps(body, separators=(',', ':')).encode())}"
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def decode(token: str, secret: str) -> Optional[dict[str, Any]]:
    """Verify and decode an HS256 JWT; return the payload or raise JwtError."""
    parts = str(token or "").split(".")
    if len(parts) != 3:
        raise JwtError("malformed token")
    signing_input = f"{parts[0]}.{parts[1]}"
    try:
        expected = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
        provided = _b64url_decode(parts[2])
    except Exception as exc:
        raise JwtError("malformed signature") from exc
    if not hmac.compare_digest(expected, provided):
        raise JwtError("invalid signature")
    try:
        payload = json.loads(_b64url_decode(parts[1]))
    except Exception as exc:
        raise JwtError("malformed payload") from exc
    exp = payload.get("exp")
    if exp is not None and int(exp) < int(time.time()):
        raise JwtExpiredError("token expired")
    return payload
