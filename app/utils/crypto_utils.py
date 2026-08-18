"""
Module Overview
---------------
Purpose: Reusable utility helpers shared across services and connectors.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger('app.utils.crypto')

#: Sentinel value for DATA_ENCRYPTION_KEY_PREVIOUS meaning "the deterministic
#: development fallback key" (used when DATA_ENCRYPTION_KEY was empty).
DEV_FALLBACK_SENTINEL = 'dev-fallback'


def _development_fallback_key() -> str:
    """Return the deterministic development fallback key (public, NOT secure)."""
    digest = hashlib.sha256(b'wootify-dev-key').digest()
    return base64.urlsafe_b64encode(digest).decode()


def _resolve_key(raw: str) -> str:
    """Resolve a raw setting value to a Fernet key, applying the dev fallback."""
    key = str(raw or '').strip()
    if not key or key == DEV_FALLBACK_SENTINEL:
        # Development fallback to avoid hard crashes when .env is not configured.
        logger.warning('DATA_ENCRYPTION_KEY is empty; using deterministic development fallback key')
        return _development_fallback_key()
    return key


class JsonEncryptor:
    """Represents json encryptor."""
    def __init__(self, raw_key: str | None = None) -> None:
        """Initialize the instance with an explicit key or the configured one."""
        key = _resolve_key(settings.DATA_ENCRYPTION_KEY if raw_key is None else raw_key)

        try:
            self._fernet = Fernet(key.encode())
        except Exception as exc:  # pragma: no cover
            raise RuntimeError('Invalid DATA_ENCRYPTION_KEY (must be Fernet-compatible)') from exc

    def encrypt_json(self, data: Any) -> str:
        """Encrypt json."""
        payload = json.dumps(data or {}, ensure_ascii=False, separators=(',', ':')).encode()
        return self._fernet.encrypt(payload).decode()

    def decrypt_json(self, token: str) -> dict[str, Any]:
        """Decrypt json."""
        if not token:
            return {}
        try:
            raw = self._fernet.decrypt(token.encode())
        except InvalidToken as exc:
            raise RuntimeError('Failed to decrypt config payload (invalid encryption key or corrupted data)') from exc
        value = json.loads(raw.decode())
        return value if isinstance(value, dict) else {}


encryptor = JsonEncryptor()


def build_previous_encryptor() -> 'JsonEncryptor | None':
    """Build an encryptor for the PREVIOUS key when a rotation is configured.

    Returns None when DATA_ENCRYPTION_KEY_PREVIOUS is unset (no rotation
    requested) or invalid (logged, startup must not be blocked).
    """
    raw = (settings.DATA_ENCRYPTION_KEY_PREVIOUS or '').strip()
    if not raw:
        return None
    try:
        return JsonEncryptor(raw)
    except RuntimeError:
        logger.exception('DATA_ENCRYPTION_KEY_PREVIOUS is invalid; skipping key rotation')
        return None

