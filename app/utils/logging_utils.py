"""
Module Overview
---------------
Purpose: Reusable utility helpers shared across services and connectors.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Optional


def redact_secret(secret: Optional[str], keep_start: int = 4, keep_end: int = 4) -> str:
    """Redact secret."""
    if not secret:
        return ""
    secret = str(secret)
    if len(secret) <= keep_start + keep_end + 3:
        return "***"
    return f"{secret[:keep_start]}***{secret[-keep_end:]}"


def truncate_text(text: Optional[str], max_len: int) -> str:
    """Truncate text."""
    if text is None:
        return ""
    text = str(text)
    if max_len <= 0 or len(text) <= max_len:
        return text
    return f"{text[:max_len]}...(truncated, len={len(text)})"


