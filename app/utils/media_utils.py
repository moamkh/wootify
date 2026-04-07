"""
Module Overview
---------------
Purpose: Reusable utility helpers shared across services and connectors.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import base64
import binascii
import logging
import re
from typing import Any, Optional, Tuple

import httpx

logger = logging.getLogger('app.utils.media')


async def resolve_media(file_client: httpx.AsyncClient, media_url_or_bytes: Any) -> Tuple[bytes, Optional[str]]:
    """Resolve media."""
    try:
        if isinstance(media_url_or_bytes, (bytes, bytearray)):
            return bytes(media_url_or_bytes), None

        if isinstance(media_url_or_bytes, str):
            raw = media_url_or_bytes.strip()
            if raw.startswith("data:"):
                m = re.match(r"^data:(?P<type>[^;]+);base64,(?P<data>.+)$", raw, flags=re.DOTALL)
                if not m:
                    raise ValueError("Invalid data URL")
                return base64.b64decode(m.group("data")), m.group("type")

            if raw.startswith("http://") or raw.startswith("https://"):
                resp = await file_client.get(raw, follow_redirects=True)
                resp.raise_for_status()
                return resp.content, resp.headers.get("content-type")

        raise ValueError("Unsupported media type; expected bytes, data: URL, or http(s) URL")
    except (ValueError, binascii.Error) as exc:
        logger.warning(
            'resolve_media rejected payload input_type=%s error=%s',
            type(media_url_or_bytes).__name__,
            str(exc),
        )
        raise
    except Exception:
        logger.exception('resolve_media failed input_type=%s', type(media_url_or_bytes).__name__)
        raise

