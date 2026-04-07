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


class JsonEncryptor:
    """Represents json encryptor."""
    def __init__(self) -> None:
        """Initialize the instance."""
        key = (settings.DATA_ENCRYPTION_KEY or '').strip()
        if not key:
            # Development fallback to avoid hard crashes when .env is not configured.
            digest = hashlib.sha256(b'wootify-dev-key').digest()
            key = base64.urlsafe_b64encode(digest).decode()
            logger.warning('DATA_ENCRYPTION_KEY is empty; using deterministic development fallback key')

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

