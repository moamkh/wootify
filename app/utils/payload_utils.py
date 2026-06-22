"""
Module Overview
---------------
Purpose: Reusable utility helpers shared across services and connectors.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Any


SENSITIVE_KEYS = {
    'token',
    'api_access_token',
    'authorization',
    'password',
    'secret',
    'api_key',
    'key',
}


def _is_sensitive(key: str) -> bool:
    """Is sensitive."""
    lowered = str(key or '').lower()
    return any(tag in lowered for tag in SENSITIVE_KEYS)


def mask_secret(value: Any) -> Any:
    """Mask secret."""
    if value is None:
        return None
    text = str(value)
    if len(text) <= 6:
        return '***'
    return f"{text[:3]}***{text[-2:]}"


def sanitize_payload(value: Any) -> Any:
    """Sanitize payload."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _is_sensitive(k):
                out[str(k)] = mask_secret(v)
            else:
                out[str(k)] = sanitize_payload(v)
        return out

    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]

    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value]

    if isinstance(value, (bytes, bytearray)):
        return f'<bytes:{len(value)}>'

    return value

