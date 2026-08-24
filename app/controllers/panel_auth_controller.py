"""Panel auth endpoints: login and token introspection.

These routes live under ``/api/v1/panel/auth/`` which the panel auth
middleware deliberately excludes from enforcement.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.panel_auth.service import panel_auth_service

logger = logging.getLogger("app.controllers.panel_auth")

router = APIRouter(prefix='/api/v1', tags=['panel-auth'])


class PanelLoginRequest(BaseModel):
    """Login payload."""

    username: str
    password: str


class PanelLoginResponse(BaseModel):
    """Login result with the issued JWT."""

    token: str
    token_type: str = "bearer"
    username: str
    expires_in_minutes: int


class PanelAuthStatusResponse(BaseModel):
    """Auth introspection result."""

    authenticated: bool
    username: Optional[str] = None
    auth_enabled: bool = True


def _client_ip(request: Request) -> str:
    client = request.client
    return client.host if client else ""


@router.post("/panel/auth/login", response_model=PanelLoginResponse)
async def panel_login(payload: PanelLoginRequest, request: Request):
    """Exchange panel credentials (from .env) for a signed JWT."""
    from fastapi import HTTPException

    if not panel_auth_service.enabled:
        raise HTTPException(status_code=503, detail="panel auth is not configured")

    client_ip = _client_ip(request)
    if panel_auth_service.is_locked_out(client_ip):
        raise HTTPException(status_code=429, detail="too many failed attempts; try again later")

    if not panel_auth_service.verify_credentials(payload.username, payload.password, client_ip):
        raise HTTPException(status_code=401, detail="invalid username or password")

    from app.config import settings

    token = panel_auth_service.issue_token(payload.username)
    logger.info("panel login ok user=%s ip=%s", payload.username, client_ip)
    return PanelLoginResponse(
        token=token,
        username=payload.username,
        expires_in_minutes=int(settings.PANEL_AUTH_TOKEN_TTL_MINUTES),
    )


@router.get("/panel/auth/status", response_model=PanelAuthStatusResponse)
async def panel_auth_status(request: Request):
    """Report whether the caller's Bearer token is currently valid."""
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    payload = panel_auth_service.verify_token(token) if token else None
    return PanelAuthStatusResponse(
        authenticated=payload is not None,
        username=str(payload.get("sub")) if payload else None,
        auth_enabled=panel_auth_service.enabled,
    )
