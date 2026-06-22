"""
Module Overview
---------------
Purpose: Reusable utility helpers shared across services and connectors.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

from app.utils.payload_utils import mask_secret


def build_proxy_url(proxy_cfg: Optional[dict[str, Any]]) -> Optional[str]:
    """Build proxy url."""
    cfg = proxy_cfg if isinstance(proxy_cfg, dict) else {}
    if not bool(cfg.get('enabled')):
        return None

    protocol = str(cfg.get('protocol') or '').strip().lower()
    host = str(cfg.get('host') or '').strip()
    port = cfg.get('port')
    username = str(cfg.get('username') or '').strip()
    password = str(cfg.get('password') or '').strip()

    if protocol not in {'http', 'https', 'socks5'} or not host or not port:
        return None

    auth = ''
    if username:
        auth = quote(username, safe='')
        if password:
            auth += f':{quote(password, safe="")}'
        auth += '@'

    return f'{protocol}://{auth}{host}:{int(port)}'


def redact_proxy_url(proxy_url: Optional[str]) -> Optional[str]:
    """Redact proxy url."""
    if not proxy_url:
        return None

    if '@' not in proxy_url:
        return proxy_url

    scheme, remainder = proxy_url.split('://', 1)
    creds, host_part = remainder.rsplit('@', 1)
    user = creds.split(':', 1)[0]
    masked = mask_secret(user)
    return f'{scheme}://{masked}@{host_part}'

