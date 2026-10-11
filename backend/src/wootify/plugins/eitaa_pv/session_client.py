"""Eitaa Web's recoverable token lifecycle, without logging credentials."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from typing import Any

from eitaa_cli import EitaaClient
from eitaa_cli.errors import EitaaRPCError

logger = logging.getLogger(__name__)
TOKEN_EXPIRED = {"eitaa_updates_expire_token", "eitaa_token_updating"}
REVOKED = {"SESSION_REVOKED", "AUTH_KEY_UNREGISTERED", "AUTH_KEY_INVALID", "USER_DEACTIVATED", "USER_DEACTIVATED_BAN"}


class EitaaLoginRequired(RuntimeError):
    """The server definitively rejected session renewal."""


class EitaaRefreshPending(RuntimeError):
    """Renewal failed temporarily; keep the saved session and retry later."""


class SessionAwareEitaaClient(EitaaClient):
    """Refresh once per client and replay only explicit authentication rejections.

    Original RPC parameters (including send random IDs) are reused. Transport
    errors from a business RPC are not automatically replayed: delivery may
    already have happened. All services, including upload/download, use invoke.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.login_required = False
        self.refresh_pending = False
        self._refresh_task: asyncio.Task | None = None
        self._pending_profile = None
        self._retry_at = 0.0
        self._refresh_failures = 0
        self._max_renewals = 2
        self._refresh_timeout = min(float(self.settings.timeout), 45.0)

    async def _raw_invoke(self, method, params=None, *, kind="client", token=None):
        return await super().invoke(method, params, kind=kind, token=token)

    async def invoke(self, method, params=None, *, kind="client", token=None):
        # OTP operations explicitly use token=""; do not intercept their errors.
        if token is not None or not self.profile.token or method == "eitaaRefreshToken":
            return await self._raw_invoke(method, params, kind=kind, token=token)
        stable_params = dict(params or {})
        for attempt in range(self._max_renewals + 1):
            if self.login_required:
                raise EitaaLoginRequired("Eitaa login required: session renewal was rejected. Request a new phone login code.")
            if self.refresh_pending:
                await self._recover(self.profile.token)
            elif self._refresh_task is not None and not self._refresh_task.done():
                await asyncio.shield(self._refresh_task)
            attempted_token = self.profile.token
            rotated = None
            try:
                response = await self._raw_invoke(method, stable_params, kind=kind, token=attempted_token)
            except EitaaRPCError as exc:
                if exc.normalized_text in REVOKED:
                    self.login_required = True
                    raise EitaaLoginRequired("Eitaa login required: session revoked by the server.") from exc
                if not (exc.code == 401 and exc.normalized_text == "INVALID_LOGIN"):
                    raise
            else:
                marker = response.get("_") if isinstance(response, dict) else None
                if marker == "eitaa_updates_token":
                    rotated = response
                elif marker not in TOKEN_EXPIRED:
                    return response
            if attempt == self._max_renewals:
                # Repeated expire/updating replies may reflect a server race;
                # they are not proof of revocation. Retry with backoff, not OTP.
                self.refresh_pending = True
                self._retry_at = time.monotonic() + 5.0
                raise EitaaRefreshPending("Eitaa token renewal did not settle; retrying later.")
            await self._recover(attempted_token, rotated)
        raise AssertionError("unreachable")

    async def _recover(self, attempted_token: str, rotated=None) -> None:
        # No await between generation check and task creation: single-flight
        # even when many requests receive the same expired token response.
        if self.profile.token != attempted_token:
            return
        if self._refresh_task is None or self._refresh_task.done():
            self.refresh_pending = True
            self._refresh_task = asyncio.create_task(self._renew(attempted_token, rotated))
            self._refresh_task.add_done_callback(self._consume_task_exception)
        await asyncio.shield(self._refresh_task)

    @staticmethod
    def _consume_task_exception(task: asyncio.Task) -> None:
        # The caller may be cancelled by a webhook timeout; renewal and atomic
        # persistence must still complete without an unobserved task exception.
        if not task.cancelled():
            task.exception()

    async def _renew(self, attempted_token: str, rotated=None) -> None:
        if time.monotonic() < self._retry_at:
            raise EitaaRefreshPending("Eitaa token renewal is waiting for retry backoff.")
        try:
            if self._pending_profile is None:
                response = rotated or await asyncio.wait_for(
                    self._raw_invoke("eitaaRefreshToken", {
                        "app_info": self.settings.web_profile.app_info(
                            build_version=self.settings.build_version,
                            app_version=self.settings.app_version,
                        ),
                    }, token=attempted_token), timeout=self._refresh_timeout,
                )
                if not isinstance(response, dict) or response.get("_") != "eitaa_updates_token" or not response.get("token"):
                    raise EitaaRefreshPending("Eitaa did not return a replacement token; retrying later.")
                self._pending_profile = replace(self.profile, token=str(response["token"]))
            # Preserve a returned token after disk failure; don't ask the server
            # to rotate again while our only fresh credential is not durable.
            await self.store.asave(self._pending_profile)
            self.profile = self._pending_profile
            self._pending_profile = None
            self.refresh_pending = False
            self.login_required = False
            self._refresh_failures = 0
            self._retry_at = 0.0
            logger.info("eitaa_token_renewed profile=%s", self.profile.name)
        except EitaaRPCError as exc:
            if exc.normalized_text in REVOKED or (exc.code == 401 and exc.normalized_text == "INVALID_LOGIN"):
                self.login_required = True
                self.refresh_pending = False
                raise EitaaLoginRequired("Eitaa login required: server rejected token renewal. Request a new phone login code.") from exc
            self._defer_refresh()
            raise EitaaRefreshPending("Eitaa token renewal temporarily failed; retrying later.") from exc
        except Exception as exc:
            self._defer_refresh()
            raise EitaaRefreshPending("Eitaa token renewal temporarily failed; retrying later.") from exc

    def _defer_refresh(self) -> None:
        self._refresh_failures += 1
        self._retry_at = time.monotonic() + min(5.0 * 2 ** min(self._refresh_failures - 1, 4), 60.0)
        self.refresh_pending = True

    async def prepare_reauthentication(self) -> None:
        if self._refresh_task is not None and not self._refresh_task.done():
            try:
                await asyncio.shield(self._refresh_task)
            except (EitaaRefreshPending, EitaaLoginRequired):
                pass

    def reset_session_recovery(self) -> None:
        """Called after an explicit successful phone-code login."""
        self.login_required = False
        self.refresh_pending = False
        self._pending_profile = None
        self._refresh_failures = 0
        self._retry_at = 0.0
        self._refresh_task = None
