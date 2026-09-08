"""Instagram PV (personal-account / userbot) connector.

Wraps the third-party ``instagrapi`` package (Instagram private mobile API)
behind the same ``PlatformConnector`` contract used by the Bale PV connector,
so polling services, the adapter registry and the Chatwoot bridge can consume
Instagram Direct Messages like any other platform.

Design notes
------------
* One ``instagrapi.Client`` per instance; all client calls are serialized
  through a per-instance ``asyncio.Lock`` and run in a worker thread because
  instagrapi is synchronous.
* Session state is persisted to ``data/instagram_pv_sessions/`` as instagrapi
  settings JSON files so restarts reuse the logged-in session instead of
  re-authenticating (which triggers Instagram login challenges).
* ``get_updates`` simulates long polling: instagrapi has no realtime push, so
  the connector re-fetches the DM inbox until a new message appears or the
  requested timeout elapses. This keeps the HTTP request rate bounded by the
  configured timeout instead of the polling loop's idle cap.
* Per-thread watermarks decide which messages are new. Watermarks of emitted
  messages are *staged* and only become durable when the caller confirms
  delivery via :meth:`commit_updates`; a crash before commit re-delivers the
  messages on the next poll (at-least-once semantics).
* Inbound messages are emitted in the Bot-API-style shape used across this
  project (``{"update_id": ..., "message": {...}}``). Instagram thread/message
  ids are encoded into a composite ``<thread_id>|<item_id>`` platform message
  id because every Instagram DM mutation (seen/unsend/reply) needs both.
* Outbound ``chat_id`` may be an Instagram user pk (private chat) or a thread
  id (group). A thread cache populated during polling disambiguates the two.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import struct
import threading
import time
from uuid import uuid4
from urllib.parse import quote
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

from wootify.config import settings
from wootify.paths import PROJECT_ROOT, VAR_ROOT

logger = logging.getLogger("app.instagram.connector")

#: Message item types that never carry customer content worth bridging.
_SKIP_ITEM_TYPES = {"action_log", "placeholder"}

#: Minimum seconds between inbox fetches when long-polling, and the cooldown
#: applied after Instagram rate-limits the account.
_MIN_FETCH_GAP_SECONDS = 5.0
_RATE_LIMIT_COOLDOWN_SECONDS = 120.0


class _ManualApprovalPending(Exception):
    """Internal sentinel: checkpoint requires approval in the official app."""


def _totp_code(seed: str, digits: int = 6, interval: int = 30) -> str:
    """Generate a TOTP code from a base32 seed (for Instagram 2FA login)."""
    normalized = re.sub(r"\s+", "", str(seed)).upper()
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    secret = base64.b32decode(normalized + padding)
    counter = int(time.time()) // interval
    digest = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


@dataclass
class InstagramPvInstanceRuntime:
    """In-memory state for a single Instagram PV (userbot) instance."""

    instance: str
    params: dict[str, Any]
    client: Any = None
    session_path: Optional[Path] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    authenticated: bool = False
    auth_detail: str = "not_connected"
    self_user_id: Optional[str] = None
    self_username: Optional[str] = None
    # thread_id -> {"is_group": bool, "title": str, "users": {pk: {...}}}
    thread_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    # user pk -> {"name": str, "username": str, "profile_pic_url": str}
    user_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    # thread_id -> newest delivered-and-committed message id
    watermarks: dict[str, str] = field(default_factory=dict)
    # thread_id -> newest emitted-but-uncommitted message id
    pending_watermarks: dict[str, str] = field(default_factory=dict)
    # Number of Chatwoot-to-Instagram sends waiting for exclusive client access.
    # Inbox polling yields before its optional pending-inbox fetch while this is
    # non-zero, so an agent reply is not needlessly held behind that extra read.
    outbound_waiters: int = 0
    last_fetch_at: float = 0.0
    rate_limited_until: float = 0.0
    sync_since: float = field(default_factory=time.time)
    # Proxy URL applied to the current client; used to detect proxy changes.
    proxy_url: Optional[str] = None
    # Checkpoint challenge flow state: None | 'sending' | 'code_sent' |
    # 'resolving' | 'connected' | 'failed'
    challenge_state: Optional[str] = None
    challenge_detail: str = ""
    challenge_choice: Optional[str] = None
    challenge_code: Optional[str] = None
    challenge_event: Any = None  # threading.Event while a flow is active
    # Native checkpoints are not code challenges. Instagram requires a
    # trusted session or an approval it chooses to issue.
    challenge_native_flow: bool = False
    # Frozen client kept when Instagram requires manual app approval, so the
    # challenge can be resumed without dropping device/session bindings.
    challenge_client: Any = None


class InstagramPvConnector:
    """Manage Instagram Direct sessions for multiple instances.

    Usage mirrors the Bale PV connector::

        await instagram_pv.connect(key, config)   # login / resume session
        updates = await instagram_pv.get_updates(key)
        await instagram_pv.commit_updates(key)    # after successful delivery
        await instagram_pv.send_text(key, peer_id, text)
        await instagram_pv.disconnect(key)
    """

    def __init__(self) -> None:
        self._logger = logger
        self._runtimes: dict[str, InstagramPvInstanceRuntime] = {}

    # ------------------------------------------------------------------
    # Runtime management
    # ------------------------------------------------------------------

    def _get_runtime(self, instance: str) -> InstagramPvInstanceRuntime:
        runtime = self._runtimes.get(instance)
        if runtime is None:
            raise RuntimeError(f"Instagram PV instance '{instance}' is not configured")
        return runtime

    @staticmethod
    def _safe_session_dir(params: dict[str, Any]) -> Path:
        """Resolve the session dir, rejecting values that escape the data root."""
        raw = str(params.get("instagram_session_dir") or settings.INSTAGRAM_PV_SESSION_DIR)
        if ".." in Path(raw).parts:
            raise ValueError(f"instagram_session_dir must not contain '..' components: {raw}")
        session_dir = Path(raw)
        resolved = (PROJECT_ROOT / session_dir).resolve() if not session_dir.is_absolute() else session_dir.resolve()
        roots = ((PROJECT_ROOT / "data").resolve(), VAR_ROOT.resolve())
        if not any(resolved.is_relative_to(root) for root in roots):
            raise ValueError("instagram_session_dir must be under the project data or runtime directory")
        return resolved

    @staticmethod
    def _proxy_url(proxy: Optional[dict[str, Any]]) -> Optional[str]:
        """Build an instagrapi-compatible proxy URL from the instance proxy config."""
        if not isinstance(proxy, dict) or not proxy.get("enabled", True):
            return None
        host = str(proxy.get("host") or "").strip()
        if not host:
            return None
        protocol = str(proxy.get("protocol") or "http").strip() or "http"
        port = str(proxy.get("port") or "").strip()
        username = str(proxy.get("username") or "").strip()
        password = str(proxy.get("password") or "")
        auth = f"{quote(username, safe='')}:{quote(password, safe='')}@" if username else ""
        return f"{protocol}://{auth}{host}:{port}" if port else f"{protocol}://{auth}{host}"

    async def connect(
        self,
        instance: str,
        params: dict[str, Any],
        proxy: Optional[dict[str, Any]] = None,
    ) -> None:
        """Initialize or resume the Instagram session for an instance.

        Authentication strategy:
        1. Load persisted instagrapi settings (session reuse) when available.
        2. ``client.login`` with username/password; on ``TwoFactorRequired``
           retry once with a code from ``instagram_verification_code`` or a
           TOTP code derived from ``instagram_totp_seed``.
        3. ``ChallengeRequired`` (checkpoint) cannot be resolved here; the
           runtime is marked unauthenticated with an explanatory detail string
           so the health endpoint surfaces it.
        """
        try:
            from instagrapi import Client
            from instagrapi.exceptions import (
                BadPassword,
                ChallengeRequired,
                TwoFactorRequired,
            )
        except ImportError as exc:
            raise RuntimeError(
                "instagrapi is not installed. Run: pip install instagrapi"
            ) from exc

        username = str(params.get("instagram_username") or "").strip()
        password = str(params.get("instagram_password") or "")
        sessionid = str(params.get("instagram_sessionid") or "").strip()
        if sessionid and "***" in sessionid:
            sessionid = ""  # masked placeholder leaked back from the panel
        if not sessionid and (not username or not password):
            raise RuntimeError(
                f"Instagram PV instance '{instance}' missing instagram_username/instagram_password "
                "(or instagram_sessionid)"
            )

        runtime = self._runtimes.get(instance)
        if runtime is None:
            runtime = InstagramPvInstanceRuntime(instance=instance, params=params)
            self._runtimes[instance] = runtime
        old_params = runtime.params
        runtime.params = dict(params)
        credentials_unchanged = all(old_params.get(k) == params.get(k) for k in (
            "instagram_username", "instagram_password", "instagram_sessionid",
            "instagram_verification_code", "instagram_totp_seed",
        ))

        if runtime.challenge_state in ("sending", "code_sent", "resolving", "manual_approval"):
            raise RuntimeError(
                "challenge_in_progress: a checkpoint challenge flow is active; "
                "complete it from the panel (start challenge / validate code / resume)"
            )

        proxy_url = self._proxy_url(proxy or params.get("proxy"))
        if credentials_unchanged and runtime.proxy_url == proxy_url and runtime.auth_detail.startswith((
            "challenge_required:", "two_factor_required:", "bad_password:",
        )):
            # Wait for a panel action or changed credentials instead of
            # attempting another login every time the poller wakes up.
            raise RuntimeError(runtime.auth_detail)
        if runtime.authenticated and runtime.client is not None:
            if runtime.proxy_url == proxy_url and credentials_unchanged:
                return
            # Proxy configuration changed: drop the existing client so the
            # reconnect goes through the new proxy instead of the old route.
            self._logger.info(
                "instagram_pv proxy_changed_reconnect instance=%s", instance
            )
            runtime.client = None
            runtime.authenticated = False

        session_dir = self._safe_session_dir(params)
        session_dir.mkdir(parents=True, exist_ok=True)
        safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", instance)
        session_path = session_dir / f"{safe_key}.json"
        runtime.session_path = session_path

        async with runtime.lock:
            client = self._new_client()
            # Small random delays between requests reduce ban risk.
            try:
                client.delay_range = [1, 3]
            except Exception:
                pass
            runtime.proxy_url = proxy_url
            if proxy_url:
                try:
                    client.set_proxy(proxy_url)
                    self._logger.info(
                        "instagram_pv proxy_applied instance=%s scheme=%s",
                        instance,
                        proxy_url.split(":", 1)[0],
                    )
                except Exception as exc:
                    raise RuntimeError("Instagram proxy configuration failed") from exc

            if session_path.exists():
                try:
                    client.load_settings(session_path)
                    self._logger.info(
                        "instagram_pv session_loaded instance=%s file=%s", instance, session_path
                    )
                except Exception as exc:
                    self._logger.warning(
                        "instagram_pv session_load_failed instance=%s error=%s", instance, exc
                    )

            # ``load_settings`` restores retry settings from the session file,
            # including the old 20-second request pacing used by earlier
            # Wootify versions. Apply the intended pacing *after* loading so
            # an existing session cannot silently reintroduce that delay.
            client.set_retry_config(request_timeout=1)

            def _login() -> None:
                if sessionid:
                    # Verified web-session cookie: skips the login endpoint
                    # entirely, so no checkpoint can be triggered.
                    if not client.login_by_sessionid(sessionid):
                        raise RuntimeError("Instagram rejected the session")
                    return
                verification_code = str(params.get("instagram_verification_code") or "").strip()
                totp_seed = str(params.get("instagram_totp_seed") or "").strip()
                try:
                    code = verification_code or (_totp_code(totp_seed) if totp_seed else "")
                    if not client.login(username, password, verification_code=code):
                        raise RuntimeError("Instagram login returned false")
                except TwoFactorRequired:
                    code = verification_code or (_totp_code(totp_seed) if totp_seed else "")
                    if not code:
                        raise
                    if not client.login(username, password, verification_code=code):
                        raise RuntimeError("Instagram two-factor login returned false")

            try:
                await asyncio.to_thread(_login)
            except TwoFactorRequired:
                await self._dump_device_fingerprint(client, session_path, instance)
                runtime.client = None
                runtime.authenticated = False
                runtime.auth_detail = (
                    "two_factor_required: set instagram_verification_code or "
                    "instagram_totp_seed in the instance metadata"
                )
                raise RuntimeError(runtime.auth_detail)
            except ChallengeRequired:
                await self._dump_device_fingerprint(client, session_path, instance)
                runtime.client = None
                runtime.authenticated = False
                native_flow = bool(
                    ((client.last_json or {}).get("challenge") or {}).get("native_flow")
                )
                if native_flow:
                    runtime.auth_detail = (
                        "native_checkpoint_required: Instagram did not issue a code or approval "
                        "request for this login. Use the sessionid from a trusted instagram.com "
                        "browser session, then reconnect."
                    )
                else:
                    runtime.auth_detail = (
                        "challenge_required: Instagram requires a checkpoint verification. "
                        "Start the email/SMS challenge only if Instagram offers a code; the device "
                        "fingerprint is persisted for a later retry."
                    )
                raise RuntimeError(runtime.auth_detail)
            except BadPassword:
                runtime.client = None
                runtime.authenticated = False
                runtime.auth_detail = "bad_password: Instagram rejected the credentials"
                raise RuntimeError(runtime.auth_detail)
            except Exception as exc:
                await self._dump_device_fingerprint(client, session_path, instance)
                runtime.client = None
                runtime.authenticated = False
                runtime.auth_detail = f"connect_failed: {type(exc).__name__}"
                raise

            if not client.user_id:
                raise RuntimeError("Instagram login did not return an authenticated user")

            runtime.client = client
            runtime.authenticated = True
            runtime.auth_detail = "authenticated"
            runtime.self_user_id = str(getattr(client, "user_id", "") or "") or None
            runtime.pending_watermarks.clear()

            try:
                account = await asyncio.to_thread(client.account_info)
                runtime.self_username = str(getattr(account, "username", "") or "") or None
            except Exception as exc:
                await self._dump_device_fingerprint(client, session_path, instance)
                runtime.client = None
                runtime.authenticated = False
                runtime.auth_detail = f"session_verification_failed: {type(exc).__name__}"
                raise RuntimeError(runtime.auth_detail) from exc

            await self._load_watermarks(runtime)
            await self._dump_settings(runtime)
            self._logger.info(
                "instagram_pv connected instance=%s self_id=%s username=%s",
                instance,
                runtime.self_user_id,
                runtime.self_username,
            )

    @staticmethod
    def _new_client() -> Any:
        from instagrapi import Client
        from instagrapi.exceptions import ChallengeRequired

        # Library DEBUG logs include session material. Never inherit the app's
        # verbose logging or let the library prompt on the server's stdin.
        sdk_logger = logging.getLogger("wootify.instagram.sdk")
        sdk_logger.setLevel(logging.WARNING)
        # ``request_timeout`` is unfortunately named in instagrapi: it is a
        # synchronous pause before every private request, not an HTTP timeout.
        # Twenty seconds here made each send wait at least twenty seconds before
        # it reached Instagram. Keep the SDK's conservative one-second default;
        # ``delay_range`` supplies the additional anti-abuse jitter.
        client = Client(logger=sdk_logger, request_timeout=1)

        def checkpoint(data: Any) -> bool:
            raise ChallengeRequired("Instagram checkpoint requires panel verification")

        client.challenge_resolve = checkpoint
        client.challenge_code_handler = lambda *args: None
        return client

    async def disconnect(self, instance: str) -> None:
        """Stop the runtime for an instance and persist its session."""
        runtime = self._runtimes.pop(instance, None)
        if runtime is None:
            return
        try:
            await self._dump_settings(runtime)
            await self._persist_watermarks(runtime)
        except Exception:
            pass
        self._logger.info("instagram_pv disconnected instance=%s", instance)

    async def _dump_settings(self, runtime: InstagramPvInstanceRuntime) -> None:
        """Persist instagrapi session settings to disk (off the event loop)."""
        if runtime.client is None or runtime.session_path is None:
            return
        try:
            await asyncio.to_thread(runtime.client.dump_settings, runtime.session_path)
        except Exception as exc:
            self._logger.warning(
                "instagram_pv session_dump_failed instance=%s error=%s", runtime.instance, exc
            )

    async def _dump_device_fingerprint(
        self, client: Any, session_path: Path, instance: str
    ) -> None:
        """Persist device identifiers even when login fails.

        Without this, every failed login attempt generates a brand-new random
        device fingerprint, which makes Instagram's checkpoint challenge
        effectively unresolvable: each retry looks like yet another unknown
        device. Persisting the fingerprint means the retry after the user
        confirms the checkpoint in the app comes from the SAME device and
        typically succeeds.
        """
        try:
            await asyncio.to_thread(client.dump_settings, session_path)
            self._logger.info(
                "instagram_pv device_fingerprint_saved instance=%s file=%s",
                instance,
                session_path,
            )
        except Exception as exc:
            self._logger.warning(
                "instagram_pv device_fingerprint_save_failed instance=%s error=%s",
                instance,
                exc,
            )

    # ------------------------------------------------------------------
    # Watermark durability
    # ------------------------------------------------------------------

    @staticmethod
    def _watermarks_path(runtime: InstagramPvInstanceRuntime) -> Optional[Path]:
        """Return the watermark sidecar path for an instance session."""
        if runtime.session_path is None:
            return None
        return runtime.session_path.with_suffix(".watermarks.json")

    async def _load_watermarks(self, runtime: InstagramPvInstanceRuntime) -> None:
        """Load committed per-thread watermarks from the sidecar file."""
        path = self._watermarks_path(runtime)
        if path is None:
            return

        def _read() -> dict[str, Any]:
            if not path.exists():
                return {}
            return json.loads(path.read_text(encoding="utf-8"))

        try:
            data = await asyncio.to_thread(_read)
            if isinstance(data, dict):
                if data.get("version") == 2:
                    runtime.sync_since = float(data["sync_since"])
                    data = data["watermarks"]
                runtime.watermarks = {str(k): str(v) for k, v in data.items()}
        except Exception as exc:
            self._logger.warning(
                "instagram_pv watermarks_load_failed instance=%s error=%s",
                runtime.instance,
                exc,
            )

    async def _persist_watermarks(self, runtime: InstagramPvInstanceRuntime) -> None:
        """Persist committed per-thread watermarks atomically (off the event loop)."""
        path = self._watermarks_path(runtime)
        if path is None:
            return

        def _write() -> None:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps({"version": 2, "sync_since": runtime.sync_since,
                                       "watermarks": runtime.watermarks}), encoding="utf-8")
            tmp.replace(path)

        try:
            await asyncio.to_thread(_write)
        except Exception as exc:
            self._logger.warning(
                "instagram_pv watermarks_persist_failed instance=%s error=%s",
                runtime.instance,
                exc,
            )

    async def commit_updates(self, instance: str) -> None:
        """Confirm delivery of all emitted updates, making watermarks durable.

        Called by the polling service after a batch of updates has been
        successfully delivered to Chatwoot. Emitted-but-uncommitted watermarks
        are discarded on restart, so unconfirmed messages are re-fetched from
        the inbox and delivered again.
        """
        runtime = self._runtimes.get(instance)
        if runtime is None:
            return
        if runtime.pending_watermarks:
            runtime.watermarks.update(runtime.pending_watermarks)
            runtime.pending_watermarks.clear()
        await self._persist_watermarks(runtime)

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def get_auth_state(self, instance: str) -> dict[str, Any]:
        """Return the auth state for an instance (used by the status endpoint)."""
        runtime = self._runtimes.get(instance)
        if runtime is None:
            return {"authenticated": False, "detail": "not_initialized"}
        return {
            "authenticated": runtime.authenticated,
            "detail": runtime.auth_detail,
            "user_id": runtime.self_user_id,
            "username": runtime.self_username,
            "session_file": str(runtime.session_path) if runtime.session_path else None,
        }

    def get_self_user_id(self, instance: str) -> Optional[str]:
        """Return the authenticated account's Instagram user pk."""
        runtime = self._runtimes.get(instance)
        return runtime.self_user_id if runtime else None

    def get_user_name(self, instance: str, user_id: int) -> Optional[str]:
        """Return a cached display name for an Instagram user pk."""
        runtime = self._runtimes.get(instance)
        if not runtime:
            return None
        cached = runtime.user_cache.get(str(user_id))
        if not cached:
            return None
        return cached.get("name") or cached.get("username")

    # ------------------------------------------------------------------
    # Outbound messaging
    # ------------------------------------------------------------------

    def _require_client(self, instance: str) -> Any:
        runtime = self._get_runtime(instance)
        if not runtime.authenticated or runtime.client is None:
            raise RuntimeError(
                "Instagram PV client is not connected. "
                "Check instagram_username/instagram_password and auth status."
            )
        return runtime.client

    def _resolve_thread_id(
        self, runtime: InstagramPvInstanceRuntime, chat_id: str
    ) -> tuple[Optional[str], Optional[str]]:
        """Resolve a chat_id to (thread_id, user_pk).

        ``chat_id`` is a thread id when it matches a cached group thread,
        otherwise an Instagram user pk for private chats. For private chats we
        look up (or create) the 1-on-1 thread so media sends, which require a
        thread id, keep working.
        """
        cached = runtime.thread_cache.get(str(chat_id))
        if cached and cached.get("is_group"):
            return str(chat_id), None
        if cached and not cached.get("is_group"):
            users = cached.get("users") or {}
            other = next((pk for pk in users if pk != str(runtime.self_user_id)), None)
            return str(chat_id), other
        if str(chat_id) in runtime.watermarks or str(chat_id) in runtime.pending_watermarks:
            return str(chat_id), None
        # Unknown chat_id: treat as a user pk.
        return None, str(chat_id)

    async def _thread_for_user(
        self, runtime: InstagramPvInstanceRuntime, user_pk: str
    ) -> Optional[str]:
        """Resolve the direct thread id for a 1-on-1 chat with a user pk."""
        client = self._require_client(runtime.instance)
        for tid, thread in runtime.thread_cache.items():
            if not thread.get("is_group") and user_pk in (thread.get("users") or {}):
                return tid

        def _lookup() -> Optional[str]:
            try:
                data = client.direct_thread_by_participants([int(user_pk)])
            except Exception as exc:
                self._logger.debug(
                    "instagram_pv thread_by_participants_failed instance=%s user=%s error=%s",
                    runtime.instance,
                    user_pk,
                    exc,
                )
                return None
            thread = (data or {}).get("thread") if isinstance(data, dict) else None
            if isinstance(thread, dict) and thread.get("thread_id"):
                return str(thread["thread_id"])
            if isinstance(data, dict) and data.get("thread_id"):
                return str(data["thread_id"])
            if getattr(data, "id", None):
                return str(data.id)
            return None

        return await asyncio.to_thread(_lookup)

    async def _approve_pending_thread(self, runtime: InstagramPvInstanceRuntime, thread_id: Optional[str]) -> None:
        cached = runtime.thread_cache.get(str(thread_id)) or {}
        if cached.get("pending"):
            if not await asyncio.to_thread(runtime.client.direct_pending_approve, int(thread_id)):
                raise RuntimeError("Instagram could not accept the message request")
            cached["pending"] = False

    async def send_text(
        self,
        instance: str,
        chat_id: str,
        text: str,
        quoted: Optional[dict[str, Any]] = None,
        access_hash: Optional[int] = None,
    ) -> dict[str, Any]:
        """Send a DM text to a user pk or thread id.

        ``quoted`` may carry a composite ``<thread_id>|<item_id>`` message id
        to send a native Instagram reply.
        """
        del access_hash  # Instagram has no access-hash concept; kept for parity.
        runtime = self._get_runtime(instance)
        client = self._require_client(instance)

        runtime.outbound_waiters += 1
        try:
            async with runtime.lock:
                thread_id, user_pk = self._resolve_thread_id(runtime, chat_id)
                if thread_id is None and user_pk:
                    thread_id = await self._thread_for_user(runtime, user_pk)
                await self._approve_pending_thread(runtime, thread_id)

                reply_to_message = None
                quoted_id = str((quoted or {}).get("message_id") or "").strip()
                reply_thread_id, reply_item_id = self._split_message_id(quoted_id)
                if reply_thread_id and reply_item_id:
                    reply_to_message = await self._fetch_direct_message(
                        runtime, reply_thread_id, reply_item_id
                    )

                def _send() -> Any:
                    kwargs: dict[str, Any] = {}
                    if reply_to_message is not None:
                        kwargs["reply_to_message"] = reply_to_message
                    if thread_id:
                        return client.direct_send(text, thread_ids=[int(thread_id)], **kwargs)
                    return client.direct_send(text, user_ids=[int(user_pk)], **kwargs)

                try:
                    try:
                        result = await asyncio.wait_for(
                            asyncio.to_thread(_send),
                            timeout=settings.INSTAGRAM_PV_MEDIA_UPLOAD_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError as exc:
                        raise TimeoutError(
                            "Instagram media upload timed out before Instagram acknowledged it"
                        ) from exc
                    await self._dump_settings(runtime)
                    self._logger.info(
                        "instagram_pv send_text ok instance=%s chat_id=%s", instance, chat_id
                    )
                    return self._send_result(result, thread_id)
                except Exception as exc:
                    self._logger.warning(
                        "instagram_pv send_text error instance=%s chat_id=%s error=%s",
                        instance,
                        chat_id,
                        exc,
                    )
                    raise
        finally:
            runtime.outbound_waiters = max(0, runtime.outbound_waiters - 1)

    async def _fetch_direct_message(
        self, runtime: InstagramPvInstanceRuntime, thread_id: str, item_id: str
    ) -> Any:
        """Fetch a DirectMessage object (needed to build a native reply)."""
        client = self._require_client(runtime.instance)

        def _fetch() -> Any:
            try:
                return client.direct_message(int(thread_id), int(item_id), amount=0)
            except Exception as exc:
                self._logger.debug(
                    "instagram_pv fetch_reply_target_failed instance=%s thread=%s item=%s error=%s",
                    runtime.instance,
                    thread_id,
                    item_id,
                    exc,
                )
                return None

        return await asyncio.to_thread(_fetch)

    @staticmethod
    def _send_result(result: Any, thread_id: Optional[str]) -> dict[str, Any]:
        item_id = str(getattr(result, "id", "") or "")
        tid = str(getattr(result, "thread_id", None) or thread_id or "")
        if not item_id or not tid:
            raise RuntimeError("Instagram send acknowledgement is missing message/thread ID")
        return {"ok": True, "message_id": f"{tid}|{item_id}", "thread_id": tid}

    async def send_media(
        self,
        instance: str,
        chat_id: str,
        media_bytes: bytes,
        filename: str,
        caption: Optional[str] = None,
        quoted: Optional[dict[str, Any]] = None,
        access_hash: Optional[int] = None,
    ) -> dict[str, Any]:
        """Send a photo/video/voice/file DM.

        Instagram DM media sends have no caption parameter, so a caption is
        delivered as a preceding text message.
        """
        del access_hash
        runtime = self._get_runtime(instance)
        client = self._require_client(instance)

        content_type = self._sniff_media_type(media_bytes, filename)
        self._validate_outbound_media(content_type, filename)
        caption_result = None
        if caption:
            caption_result = await self.send_text(instance, chat_id, caption, quoted=quoted)

        suffix = Path(filename or "file").suffix or ""
        tmp_dir = (runtime.session_path.parent / "tmp") if runtime.session_path else Path("./data")
        tmp_path = tmp_dir / f"ig_{uuid4().hex}{suffix}"

        def _write_tmp() -> None:
            tmp_dir.mkdir(parents=True, exist_ok=True)
            tmp_path.write_bytes(media_bytes)

        await asyncio.to_thread(_write_tmp)

        runtime.outbound_waiters += 1
        try:
            async with runtime.lock:
                thread_id, user_pk = self._resolve_thread_id(runtime, chat_id)
                if thread_id is None and user_pk:
                    thread_id = await self._thread_for_user(runtime, user_pk)
                await self._approve_pending_thread(runtime, thread_id)

                target_kwargs: dict[str, Any]
                if thread_id:
                    target_kwargs = {"thread_ids": [int(thread_id)]}
                elif user_pk:
                    target_kwargs = {"user_ids": [int(user_pk)]}
                else:
                    raise RuntimeError(f"cannot resolve Instagram target for chat_id={chat_id}")

                content_type = self._sniff_media_type(media_bytes, filename)

                def _send() -> Any:
                    if content_type.startswith("image/"):
                        return client.direct_send_photo(tmp_path, **target_kwargs)
                    if content_type.startswith("video/"):
                        return client.direct_send_video(tmp_path, **target_kwargs)
                    if content_type.startswith("audio/"):
                        return client.direct_send_voice(tmp_path, **target_kwargs)
                    raise ValueError("Unsupported Instagram media type")

                try:
                    result = await asyncio.to_thread(_send)
                    await self._dump_settings(runtime)
                    self._logger.info(
                        "instagram_pv send_media ok instance=%s chat_id=%s kind=%s",
                        instance,
                        chat_id,
                        content_type,
                    )
                    ack = self._send_result(result, thread_id)
                    if caption_result:
                        ack["additional_message_ids"] = [caption_result["message_id"]]
                    return ack
                except Exception as exc:
                    self._logger.warning(
                        "instagram_pv send_media error instance=%s chat_id=%s error=%s",
                        instance,
                        chat_id,
                        exc,
                    )
                    raise
        finally:
            runtime.outbound_waiters = max(0, runtime.outbound_waiters - 1)
            try:
                await asyncio.to_thread(tmp_path.unlink, True)
            except Exception:
                pass

    @staticmethod
    def _sniff_media_type(content: bytes, filename: str) -> str:
        """Best-effort content type from magic bytes, then filename."""
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if len(content) > 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return "image/webp"
        if len(content) > 8 and content[4:8] == b"ftyp":
            if content[8:12] in (b"M4A ", b"M4B ") or Path(filename).suffix.lower() in (".m4a", ".m4b"):
                return "audio/mp4"
            return "video/mp4"
        if content.startswith(b"OggS"):
            return "audio/ogg"
        if content.startswith(b"ID3") or content[:2] == b"\xff\xfb":
            return "audio/mpeg"
        import mimetypes

        guessed = mimetypes.guess_type(str(filename or ""))[0]
        return guessed or "application/octet-stream"

    @staticmethod
    def _validate_outbound_media(content_type: str, filename: str) -> None:
        """Reject types the Instagram SDK cannot send as a direct attachment.

        ``direct_send_file`` is only a photo/video convenience method.  Voice
        messages must be AAC in an M4A container, and the photo/video helpers
        have the extension and codec constraints below.  Validate before a
        caption is sent so an invalid attachment never leaves a partial DM.
        """
        suffix = Path(filename or "").suffix.lower()
        if content_type in {"image/jpeg", "image/png", "image/webp"} and suffix in {
            ".jpg", ".jpeg", ".png", ".webp",
        }:
            return
        if content_type == "video/mp4" and suffix == ".mp4":
            return
        if content_type == "audio/mp4" and suffix == ".m4a":
            return

        if content_type == "image/gif":
            raise ValueError(
                "Instagram DMs do not accept GIF files through this connector; "
                "send a JPG, PNG, WebP photo or an MP4 video instead"
            )
        if content_type in {"audio/mpeg", "audio/ogg"} or content_type.startswith("audio/"):
            raise ValueError(
                "Instagram DMs require AAC/M4A voice messages; MP3, Ogg and other audio formats are unsupported"
            )
        if content_type.startswith("video/"):
            raise ValueError(
                "Instagram DMs require an MP4 video encoded with H.264 video and AAC audio"
            )
        if content_type.startswith("image/"):
            raise ValueError(
                "Instagram DMs support JPG/JPEG, PNG and WebP photos; this image format is unsupported"
            )
        raise ValueError(
            "Instagram DMs support JPG/JPEG, PNG and WebP photos, MP4 videos and M4A/AAC voice messages; this file type is unsupported"
        )

    async def update_message(
        self,
        instance: str,
        chat_id: str,
        message_id: str,
        text: str,
    ) -> dict[str, Any]:
        """The installed SDK lacks DM editing; kept for protocol parity."""
        del instance, chat_id, message_id, text
        return {"ok": False, "description": "instagram_dm_edit_not_supported"}

    async def delete_message(
        self,
        instance: str,
        chat_id: str,
        message_id: str,
    ) -> dict[str, Any]:
        """Unsend a message. ``message_id`` must be the composite id we emit."""
        del chat_id
        runtime = self._get_runtime(instance)
        client = self._require_client(instance)
        thread_id, item_id = self._split_message_id(message_id)
        if not thread_id or not item_id:
            raise RuntimeError(f"invalid Instagram message id: {message_id}")
        async with runtime.lock:
            result = await asyncio.to_thread(
                client.direct_message_unsend, int(thread_id), int(item_id)
            )
            return {"ok": bool(result)}

    # ------------------------------------------------------------------
    # Inbound polling
    # ------------------------------------------------------------------

    async def get_updates(
        self,
        instance: str,
        offset: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> dict[str, Any]:
        """Fetch new DM messages, simulating a long poll.

        Re-fetches the inbox until at least one new message appears or
        ``timeout`` seconds elapse, so the effective request rate is bounded
        by the timeout rather than the polling loop's idle cap. ``offset`` is
        accepted for contract parity; dedup uses internal per-thread
        watermarks instead.

        Watermarks of emitted messages are staged, not committed; call
        :meth:`commit_updates` after successful delivery.
        """
        del offset
        runtime = self._runtimes.get(instance)
        if runtime is None or not runtime.authenticated or runtime.client is None:
            return {
                "ok": False,
                "description": (runtime.auth_detail if runtime else "not_configured"),
            }

        now = time.monotonic()
        if now < runtime.rate_limited_until:
            await asyncio.sleep(min(runtime.rate_limited_until - now, 30.0))
            return {"ok": True, "result": []}

        deadline = now + max(int(timeout or 0), 0)
        while True:
            interval = max(float(runtime.params.get("instagram_poll_interval") or settings.INSTAGRAM_PV_POLL_INTERVAL_SECONDS), _MIN_FETCH_GAP_SECONDS)
            remaining = interval - (time.monotonic() - runtime.last_fetch_at)
            if runtime.last_fetch_at and remaining > 0:
                await asyncio.sleep(remaining)
            try:
                updates = await self._fetch_new_messages(runtime)
            except Exception as exc:
                name = type(exc).__name__
                self._logger.warning(
                    "instagram_pv get_updates_error instance=%s error_type=%s error=%s",
                    instance,
                    name,
                    exc,
                )
                if name in ("PleaseWaitFewMinutes", "RateLimitError", "FeedbackRequired", "ClientThrottledError"):
                    runtime.rate_limited_until = time.monotonic() + _RATE_LIMIT_COOLDOWN_SECONDS
                    return {"ok": False, "description": "Instagram rate limited; polling is paused"}
                if name in ("LoginRequired", "ChallengeRequired"):
                    runtime.authenticated = False
                    runtime.auth_detail = f"{name}: session needs authentication"
                return {"ok": False, "description": f"{name}: {exc}"}

            if updates:
                return {"ok": True, "result": updates}

            if time.monotonic() >= deadline:
                return {"ok": True, "result": []}

            # Respect the minimum gap between inbox fetches.
            elapsed = time.monotonic() - runtime.last_fetch_at
            gap = max(_MIN_FETCH_GAP_SECONDS - elapsed, 1.0)
            await asyncio.sleep(min(gap, max(deadline - time.monotonic(), 0.5) or 0.5))

    async def _fetch_new_messages(
        self, runtime: InstagramPvInstanceRuntime
    ) -> list[dict[str, Any]]:
        """Fetch the DM inbox once and return updates newer than watermarks."""
        client = self._require_client(runtime.instance)
        # ``amount=0`` means *all pages* in instagrapi. That turns a routine
        # inbox poll into an unbounded series of API calls for accounts with a
        # long DM history, each one holding the send lock. A newly active thread
        # is at the top of the inbox; its own history is still expanded below
        # until its watermark is found, preserving at-least-once delivery.
        async with runtime.lock:
            threads = await asyncio.to_thread(
                client.direct_threads, amount=20, thread_message_limit=20
            )

        pending: list[Any] = []
        # Pending requests are an auxiliary inbox. Do not make an agent reply
        # wait behind this second remote request when it is already queued.
        if runtime.outbound_waiters == 0:
            async with runtime.lock:
                if runtime.outbound_waiters == 0:
                    pending = await asyncio.to_thread(
                        client.direct_pending_inbox, amount=20
                    )
        threads = list({str(t.id): t for t in [*(threads or []), *pending]}.values())
        runtime.last_fetch_at = time.monotonic()
        await self._dump_settings(runtime)

        updates: list[dict[str, Any]] = []
        self_pk = str(runtime.self_user_id or "")
        staged_watermarks = dict(runtime.pending_watermarks)

        for thread in threads or []:
            thread_id = str(getattr(thread, "id", None) or getattr(thread, "thread_id", "") or "")
            if not thread_id:
                continue

            is_group = bool(getattr(thread, "is_group", False))
            title = str(getattr(thread, "thread_title", "") or "").strip()

            users: dict[str, dict[str, Any]] = {}
            for user in getattr(thread, "users", None) or []:
                pk = str(getattr(user, "pk", "") or getattr(user, "id", "") or "")
                if not pk:
                    continue
                info = {
                    "name": str(getattr(user, "full_name", "") or "").strip()
                    or str(getattr(user, "username", "") or "").strip(),
                    "username": str(getattr(user, "username", "") or "").strip(),
                    "profile_pic_url": str(getattr(user, "profile_pic_url", "") or ""),
                }
                users[pk] = info
                runtime.user_cache[pk] = info

            runtime.thread_cache[thread_id] = {
                "is_group": is_group,
                "pending": bool(getattr(thread, "pending", False)),
                "title": title,
                "users": users,
            }

            messages = list(getattr(thread, "messages", None) or [])
            if not messages:
                continue

            def _msg_key(msg: Any) -> int:
                raw = str(getattr(msg, "id", "") or "")
                if raw.isdigit():
                    return int(raw)
                ts = getattr(msg, "timestamp", None)
                if ts is not None:
                    try:
                        return int(ts.timestamp() * 1_000_000)
                    except Exception:
                        pass
                return 0

            messages.sort(key=_msg_key)

            # Seen baseline = committed watermark or anything already staged
            # by an earlier fetch that the caller has not confirmed yet.
            pending = staged_watermarks.get(thread_id)
            watermark = pending or runtime.watermarks.get(thread_id)
            watermark_key = int(watermark) if str(watermark).isdigit() else 0
            # Expand the window until it includes the delivery checkpoint.
            # direct_messages paginates internally; amount=0 means all history.
            def newer(msg: Any) -> bool:
                if watermark is not None:
                    return _msg_key(msg) > watermark_key
                ts = getattr(msg, "timestamp", None)
                return ts is None or ts.timestamp() >= runtime.sync_since

            limit = max(len(messages) * 2, 40)
            while messages and newer(messages[0]):
                async with runtime.lock:
                    expanded = await asyncio.to_thread(client.direct_messages, int(thread_id), amount=limit)
                expanded = sorted(expanded or [], key=_msg_key)
                messages = expanded
                if len(expanded) < limit:
                    break
                limit *= 2

            for msg in messages:
                msg_id = str(getattr(msg, "id", "") or "")
                if not msg_id:
                    continue
                if not newer(msg):
                    if watermark is None:
                        staged_watermarks[thread_id] = msg_id
                    continue
                item_type = str(getattr(msg, "item_type", "") or "").strip()
                if item_type in _SKIP_ITEM_TYPES:
                    staged_watermarks[thread_id] = msg_id
                    continue
                update = self._build_update(runtime, thread_id, is_group, title, users, msg, self_pk)
                staged_watermarks[thread_id] = msg_id
                if update is not None:
                    updates.append(update)

        # A later thread can fail during catch-up. Do not stage any message
        # until the complete batch can be returned to the delivery service.
        runtime.pending_watermarks = staged_watermarks
        # Persist the initial cutoff even for an empty inbox. Unknown threads
        # arriving later (including after restart) are then delivered normally.
        await self._persist_watermarks(runtime)

        if updates:
            updates.sort(key=lambda item: int(item.get("update_id") or 0))
        return updates

    def _build_update(
        self,
        runtime: InstagramPvInstanceRuntime,
        thread_id: str,
        is_group: bool,
        title: str,
        users: dict[str, dict[str, Any]],
        msg: Any,
        self_pk: str,
    ) -> Optional[dict[str, Any]]:
        """Convert an instagrapi DirectMessage into a Bot-API-style update."""
        msg_id = str(getattr(msg, "id", "") or "")
        sender_pk = str(getattr(msg, "user_id", "") or "")
        is_outgoing = bool(getattr(msg, "is_sent_by_viewer", False) or (self_pk and sender_pk == self_pk))
        item_type = str(getattr(msg, "item_type", "") or "").strip()

        text = str(getattr(msg, "text", "") or "").strip()
        attachments = self._extract_attachment_refs(msg, item_type)
        share_text = self._share_fallback_text(msg, item_type)
        if not text and share_text:
            text = share_text

        # Resolve the peer that represents the Chatwoot contact.
        if is_group:
            chat_id = thread_id
            chat_type = "group"
        elif is_outgoing:
            others = [pk for pk in users if pk != self_pk]
            chat_id = others[0] if others else thread_id
            chat_type = "private"
        else:
            chat_id = sender_pk or thread_id
            chat_type = "private"

        sender_info = users.get(sender_pk) or runtime.user_cache.get(sender_pk) or {}

        ts = getattr(msg, "timestamp", None)
        try:
            update_id = int(ts.timestamp() * 1_000_000) if ts is not None else int(time.time() * 1_000_000)
        except Exception:
            update_id = int(time.time() * 1_000_000)

        message: dict[str, Any] = {
            "message_id": f"{thread_id}|{msg_id}",
            "chat": {
                "id": chat_id,
                "type": chat_type,
                "title": title or None,
            },
            "from": {
                "id": sender_pk,
                "first_name": sender_info.get("name") or "",
                "username": sender_info.get("username") or "",
            },
            "text": text,
            "_outgoing": is_outgoing,
        }
        message.update(attachments)
        reply = getattr(msg, "reply", None)
        if reply is not None and getattr(reply, "id", None):
            message["reply_to_message"] = {"message_id": f"{thread_id}|{reply.id}"}

        return {"update_id": update_id, "message": message}

    def _extract_attachment_refs(self, msg: Any, item_type: str) -> dict[str, Any]:
        """Extract Bot-API-style attachment refs from a DirectMessage.

        Each ``file_id`` is a JSON document carrying the CDN URL; the adapter
        later resolves it via :meth:`download_file_by_id`.
        """
        refs: dict[str, Any] = {}

        def _file_id(kind: str, url: str) -> str:
            return json.dumps({"kind": kind, "url": url})

        media = getattr(msg, "media", None)
        if media is not None:
            for attr, kind, ct in (("audio_url", "voice", "audio/mp4"),
                                   ("video_url", "video", "video/mp4"),
                                   ("thumbnail_url", "photo", "image/jpeg")):
                url = str(getattr(media, attr, None) or "")
                if url:
                    ref = {"file_id": _file_id(kind, url), "mime_type": ct}
                    refs[kind] = [ref] if kind == "photo" else ref
                    break
        if item_type == "media" and media is not None:
            media_type = getattr(media, "media_type", None)
            video_versions = getattr(media, "video_versions", None) or []
            image_versions = getattr(media, "image_versions2", None)
            if media_type == 2 and video_versions:
                url = str(getattr(video_versions[0], "url", "") or "")
                if url:
                    refs["video"] = {"file_id": _file_id("video", url), "mime_type": "video/mp4"}
            elif image_versions is not None:
                candidates = getattr(image_versions, "candidates", None) or []
                if candidates:
                    url = str(getattr(candidates[0], "url", "") or "")
                    if url:
                        refs["photo"] = [{"file_id": _file_id("photo", url)}]

        voice_media = getattr(msg, "voice_media", None)
        if item_type == "voice_media" and voice_media is not None:
            audio = getattr(getattr(voice_media, "media", None), "audio", None)
            url = str(getattr(audio, "audio_url", "") or "") if audio is not None else ""
            if url:
                refs["voice"] = {"file_id": _file_id("voice", url), "mime_type": "audio/mp4"}

        animated = getattr(msg, "animated_media", None)
        if isinstance(animated, dict):
            fixed = (animated.get("images") or {}).get("fixed_height") or {}
            url = fixed.get("mp4") or fixed.get("url")
            if url:
                kind = "video" if fixed.get("mp4") else "document"
                refs[kind] = {"file_id": _file_id(kind, url), "mime_type": "video/mp4" if kind == "video" else "image/gif", "file_name": "animation.mp4" if kind == "video" else "animation.gif"}
        if item_type == "animated_media" and animated is not None:
            images = getattr(animated, "images", None)
            fixed = getattr(images, "fixed_height", None) if images is not None else None
            url = str(getattr(fixed, "url", "") or "") if fixed is not None else ""
            if url:
                refs["video"] = {"file_id": _file_id("video", url), "mime_type": "video/mp4"}

        # Post/reel/clip shares: attach the thumbnail so agents see a preview.
        for attr in ("media_share", "clip", "reel_share", "story_share"):
            shared = getattr(msg, attr, None)
            if shared is None:
                continue
            url = str(getattr(shared, "thumbnail_url", None) or "")
            if url:
                refs.setdefault("photo", [{"file_id": _file_id("photo", url)}])
            image_versions = getattr(shared, "image_versions2", None)
            candidates = getattr(image_versions, "candidates", None) or [] if image_versions else []
            if candidates:
                url = str(getattr(candidates[0], "url", "") or "")
                if url:
                    refs.setdefault("photo", [{"file_id": _file_id("photo", url)}])
            break

        return refs

    @staticmethod
    def _share_fallback_text(msg: Any, item_type: str) -> str:
        """Human-readable fallback for non-text DM items."""
        if item_type == "like":
            return "❤️"
        for attr, label in (
            ("media_share", "post"),
            ("clip", "reel"),
            ("reel_share", "reel"),
            ("story_share", "story"),
        ):
            shared = getattr(msg, attr, None)
            if shared is None:
                continue
            code = str(getattr(shared, "code", "") or "").strip()
            link = f"https://instagram.com/p/{code}/" if code else ""
            return f"📎 Shared a {label}: {link}".rstrip(": ")
        if item_type == "xma_share":
            xma = getattr(msg, "xma_share", None)
            url = str(getattr(xma, "target_url", "") or "") if xma is not None else ""
            return f"📎 Shared a link: {url}".rstrip(": ")
        if item_type == "voice_media":
            return "🎤 Voice message"
        if item_type == "animated_media":
            return "GIF"
        if item_type and item_type not in ("text", "media"):
            return f"[{item_type}]"
        return ""

    @staticmethod
    def _split_message_id(message_id: str) -> tuple[Optional[str], Optional[str]]:
        """Split a composite ``<thread_id>|<item_id>`` message id."""
        raw = str(message_id or "")
        if "|" not in raw:
            return None, None
        thread_id, item_id = raw.split("|", 1)
        return thread_id or None, item_id or None

    # ------------------------------------------------------------------
    # Media download / avatars
    # ------------------------------------------------------------------

    async def download_file_by_id(
        self,
        instance: str,
        file_id: str,
    ) -> tuple[bytes, Optional[str], Optional[str]]:
        """Download a DM media payload by its JSON file id."""
        runtime = self._get_runtime(instance)
        try:
            ref = json.loads(str(file_id))
        except (TypeError, ValueError):
            self._logger.warning(
                "instagram_pv invalid file_id instance=%s file_id=%s", instance, str(file_id)[:80]
            )
            return b"", None, None
        url = str(ref.get("url") or "").strip()
        if not url:
            return b"", None, None

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        }
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=settings.INSTAGRAM_PV_MEDIA_DOWNLOAD_TIMEOUT_SECONDS,
                proxy=runtime.proxy_url,
            ) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                content = resp.content
                content_type = resp.headers.get("content-type", "").split(";")[0] or None
                filename = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] or None
                return content, content_type, filename
        except Exception as exc:
            self._logger.warning(
                "instagram_pv download_failed instance=%s url=%s error=%s",
                instance,
                url[:80],
                exc,
            )
            raise RuntimeError("Instagram attachment download failed") from exc

    async def get_user_avatar_bytes(
        self,
        instance: str,
        user_id: int,
    ) -> tuple[Optional[bytes], Optional[str]]:
        """Download a user's profile picture for Chatwoot contact avatars."""
        runtime = self._runtimes.get(instance)
        url = ""
        if runtime is not None:
            cached = runtime.user_cache.get(str(user_id))
            if cached:
                url = str(cached.get("profile_pic_url") or "")
        if not url and runtime is not None and runtime.authenticated:
            try:
                async with runtime.lock:
                    info = await asyncio.to_thread(runtime.client.user_info, int(user_id))
                url = str(getattr(info, "profile_pic_url", "") or "")
                runtime.user_cache[str(user_id)] = {
                    "name": str(getattr(info, "full_name", "") or "").strip()
                    or str(getattr(info, "username", "") or "").strip(),
                    "username": str(getattr(info, "username", "") or "").strip(),
                    "profile_pic_url": url,
                }
            except Exception as exc:
                self._logger.debug(
                    "instagram_pv user_info_failed instance=%s user=%s error=%s",
                    instance,
                    user_id,
                    exc,
                )
        if not url:
            return None, None
        content, content_type, _ = await self.download_file_by_id(
            instance, json.dumps({"kind": "photo", "url": url})
        )
        return (content or None), content_type

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def get_connection_state(self, instance: str) -> dict[str, Any]:
        """Return connection health state for an Instagram PV instance."""
        runtime = self._runtimes.get(instance)
        if runtime is None:
            return {"connected": False, "detail": "not_initialized"}
        return {
            "connected": bool(runtime.authenticated and runtime.client is not None),
            "detail": runtime.auth_detail,
            "username": runtime.self_username,
            "user_id": runtime.self_user_id,
            "rate_limited": time.monotonic() < runtime.rate_limited_until,
            "challenge_state": runtime.challenge_state,
            "challenge_detail": runtime.challenge_detail,
            "challenge_choice": runtime.challenge_choice,
        }

    async def reconnect(
        self,
        instance: str,
        params: dict[str, Any],
        proxy: Optional[dict[str, Any]] = None,
        *,
        fresh: bool = False,
    ) -> dict[str, Any]:
        """Force a fresh username/password login for the panel "Reconnect" button.

        Drops the in-memory client and re-authenticates. The persisted device
        fingerprint is kept by default so the retry still looks like the same
        device to Instagram; ``fresh=True`` also deletes the session file,
        producing a completely new device identity (use only when the old
        device is irreversibly flagged).
        """
        old = self._runtimes.get(instance)
        session_path = old.session_path if old else None
        await self.disconnect(instance)
        if fresh and session_path is not None and session_path.exists():
            try:
                await asyncio.to_thread(session_path.unlink)
                self._logger.info(
                    "instagram_pv session_file_removed instance=%s file=%s",
                    instance,
                    session_path,
                )
            except Exception as exc:
                self._logger.warning(
                    "instagram_pv session_file_remove_failed instance=%s error=%s",
                    instance,
                    exc,
                )
        try:
            await self.connect(instance, params, proxy)
        except Exception as exc:
            state = await self.get_connection_state(instance)
            state["detail"] = str(exc) or state.get("detail") or "connect_failed"
            failed_runtime = self._runtimes.get(instance)
            state["session_file"] = bool(
                failed_runtime
                and failed_runtime.session_path
                and failed_runtime.session_path.exists()
            )
            return state
        state = await self.get_connection_state(instance)
        runtime = self._runtimes.get(instance)
        state["session_file"] = bool(
            runtime and runtime.session_path and runtime.session_path.exists()
        )
        return state

    # ------------------------------------------------------------------
    # Checkpoint challenge flow (email/SMS security code)
    # ------------------------------------------------------------------

    def _adopt_client(
        self,
        runtime: InstagramPvInstanceRuntime,
        client: Any,
        session_path: Path,
    ) -> None:
        """Promote a successfully logged-in client to the live runtime session.

        Must be called from the worker thread; performs only quick attribute
        writes plus the settings dump.
        """
        account = client.account_info()
        if not client.user_id:
            raise RuntimeError("Instagram checkpoint did not establish a session")
        runtime.client = client
        runtime.authenticated = True
        runtime.auth_detail = "authenticated"
        runtime.self_user_id = str(client.user_id)
        runtime.self_username = str(getattr(account, "username", "") or "") or None
        try:
            client.dump_settings(session_path)
        except Exception:
            pass
        runtime.pending_watermarks.clear()
        runtime.challenge_state = "connected"
        runtime.challenge_detail = "checkpoint cleared; logged in"
        runtime.challenge_client = None

    def _wait_for_challenge_code(
        self, runtime: InstagramPvInstanceRuntime, choice: Any
    ) -> Optional[str]:
        """instagrapi challenge_code_handler: block until the panel submits a code.

        Called from the worker thread once Instagram has dispatched the
        security code (email first, SMS fallback). Waits up to 5 minutes for
        ``submit_challenge_code`` to provide the code.
        """
        choice_name = getattr(choice, "name", str(choice))
        event = runtime.challenge_event
        if event is None:
            return None
        event.clear()
        runtime.challenge_code = None
        runtime.challenge_state = "code_sent"
        runtime.challenge_choice = choice_name
        runtime.challenge_detail = (
            f"Security code sent via {choice_name.lower()}; enter it in the panel"
        )
        if not event.wait(timeout=300):
            return None
        return runtime.challenge_code

    async def start_challenge(
        self,
        instance: str,
        params: dict[str, Any],
        proxy: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Start the Instagram checkpoint challenge flow for an instance.

        Attempts a normal login; when Instagram answers ``challenge_required``
        with a native checkpoint, drives the contact-form flow directly
        (bypassing instagrapi's native_flow guard). The flow pauses inside the
        code handler after Instagram sends the security code; the panel then
        submits it via ``submit_challenge_code``.
        """
        try:
            from instagrapi import Client
            from instagrapi.exceptions import (
                BadPassword,
                ChallengeRequired,
                TwoFactorRequired,
            )
        except ImportError as exc:
            raise RuntimeError(
                "instagrapi is not installed. Run: pip install instagrapi"
            ) from exc

        username = str(params.get("instagram_username") or "").strip()
        password = str(params.get("instagram_password") or "")
        sessionid = str(params.get("instagram_sessionid") or "").strip()
        if sessionid and "***" in sessionid:
            sessionid = ""  # masked placeholder leaked back from the panel
        if not sessionid and (not username or not password):
            raise RuntimeError(
                f"Instagram PV instance '{instance}' missing instagram_username/instagram_password "
                "(or instagram_sessionid)"
            )

        runtime = self._runtimes.get(instance)
        if runtime is None:
            runtime = InstagramPvInstanceRuntime(instance=instance, params=params)
            self._runtimes[instance] = runtime
        runtime.params = params

        if runtime.authenticated and runtime.client is not None:
            return {"state": "connected", "detail": "already authenticated", "choice": None}
        if runtime.challenge_state in ("sending", "code_sent", "resolving", "manual_approval"):
            return {
                "state": runtime.challenge_state,
                "detail": runtime.challenge_detail,
                "choice": runtime.challenge_choice,
            }

        session_dir = self._safe_session_dir(params)
        session_dir.mkdir(parents=True, exist_ok=True)
        safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", instance)
        session_path = session_dir / f"{safe_key}.json"
        runtime.session_path = session_path
        proxy_url = self._proxy_url(proxy or params.get("proxy"))

        async with runtime.lock:
            client = self._new_client()
            await self._load_watermarks(runtime)
            try:
                client.delay_range = [1, 3]
            except Exception:
                pass
            runtime.proxy_url = proxy_url
            if proxy_url:
                try:
                    client.set_proxy(proxy_url)
                except Exception as exc:
                    self._logger.warning(
                        "instagram_pv set_proxy_failed instance=%s error=%s", instance, exc
                    )
            if session_path.exists():
                try:
                    client.load_settings(session_path)
                except Exception as exc:
                    self._logger.warning(
                        "instagram_pv session_load_failed instance=%s error=%s", instance, exc
                    )

            runtime.challenge_event = threading.Event()
            runtime.challenge_code = None
            runtime.challenge_state = "sending"
            runtime.challenge_detail = "starting checkpoint challenge flow"
            runtime.challenge_choice = None
            runtime.challenge_client = None

            def _flow() -> None:
                def _resolve_checkpoint() -> str:
                    """Drive the checkpoint past login without instagrapi's
                    native_flow guard (mirrors upstream challenge_resolve
                    minus the pre-emptive aborts, per instagrapi PR #2652).

                    Returns 'resolved' when cleared programmatically —
                    including the automatic Bloks ``choice=0`` bypass for
                    low-risk checkpoints — or 'manual' when Instagram
                    requires approval in the official app (client kept
                    frozen on the runtime for ``resume_challenge``).
                    """
                    api_path = ((client.last_json or {}).get("challenge") or {}).get("api_path")
                    if not api_path:
                        raise RuntimeError("challenge_required without an api_path to resolve")
                    challenge_url = client._normalize_challenge_api_path(api_path)
                    self._logger.info(
                        "instagram_pv challenge_resolve_enter instance=%s api_path=%s",
                        instance,
                        challenge_url[:60],
                    )
                    if challenge_url.startswith("/auth_platform/"):
                        raise RuntimeError(
                            "auth_platform challenge is not supported automatically"
                        )
                    if ((client.last_json or {}).get("challenge") or {}).get("native_flow"):
                        runtime.challenge_native_flow = True
                        self._logger.info(
                            "instagram_pv native_checkpoint instance=%s", instance
                        )
                        return "manual"
                    runtime.challenge_native_flow = False
                    challenge_context = ((client.last_json or {}).get("challenge") or {}).get(
                        "challenge_context"
                    )
                    client.challenge_code_handler = (
                        lambda _user, choice: self._wait_for_challenge_code(runtime, choice)
                    )
                    resolve_params = {
                        "guid": client.uuid,
                        "device_id": client.android_device_id,
                    }
                    if challenge_context:
                        resolve_params["challenge_context"] = challenge_context
                    try:
                        client._send_private_request(
                            challenge_url.lstrip("/"), params=resolve_params
                        )
                    except ChallengeRequired as pre_exc:
                        self._logger.info(
                            "instagram_pv challenge_pre_request_raised instance=%s msg=%s",
                            instance,
                            str(pre_exc)[:120],
                        )
                        # Classic HTML contact-form variant (email/SMS code page).
                        self._logger.info(
                            "instagram_pv challenge_contact_form_started instance=%s url=%s",
                            instance,
                            challenge_url,
                        )
                        try:
                            ok = client.challenge_resolve_contact_form(challenge_url)
                        except Exception as form_exc:
                            self._logger.info(
                                "instagram_pv challenge_contact_form_exc instance=%s type=%s msg=%s",
                                instance,
                                type(form_exc).__name__,
                                str(form_exc)[:150],
                            )
                            msg = str(form_exc)
                            if "Expecting value" in msg or "405" in msg:
                                # Instagram serves the native checkpoint page
                                # instead of the form: manual app approval only.
                                return "manual"
                            raise
                        if not ok:
                            raise RuntimeError("challenge contact-form flow did not complete")
                        return "resolved"
                    self._logger.info(
                        "instagram_pv challenge_pre_request_ok instance=%s step=%s",
                        instance,
                        (client.last_json or {}).get("step_name"),
                    )
                    # Snapshot the Bloks challenge context: if the
                    # programmatic bypass gets rejected, last_json is
                    # overwritten with the raw challenge payload and the
                    # dismiss-resume flow would lose its context.
                    last_after_pre = client.last_json or {}
                    bloks_snapshot = (
                        dict(last_after_pre)
                        if last_after_pre.get("bloks_action") and last_after_pre.get("challenge_context")
                        else None
                    )
                    # Private-API variant: challenge_resolve_simple auto-tries the
                    # Bloks take_challenge(choice=0) bypass and email/SMS code
                    # steps (the latter pause in our code handler above).
                    self._logger.info(
                        "instagram_pv challenge_simple_started instance=%s url=%s",
                        instance,
                        challenge_url,
                    )
                    try:
                        ok = client.challenge_resolve_simple(challenge_url)
                    except ChallengeRequired as simple_exc:
                        self._logger.info(
                            "instagram_pv challenge_simple_raised instance=%s msg=%s",
                            instance,
                            str(simple_exc)[:150],
                        )
                        if bloks_snapshot is not None:
                            # Bypass rejected (high-risk checkpoint):
                            # restore the Bloks context so the frozen
                            # client stays resumable after app approval.
                            client.last_json = bloks_snapshot
                            return "manual"
                        raise
                    if not ok:
                        raise RuntimeError("challenge resolution did not complete")
                    return "resolved"

                def _freeze_manual(detail: str) -> None:
                    """Park the frozen client so the checkpoint can be resumed
                    after the user approves it in the official app."""
                    runtime.challenge_client = client
                    try:
                        client.dump_settings(session_path)
                    except Exception:
                        pass
                    runtime.challenge_state = "manual_approval"
                    runtime.challenge_detail = detail
                    runtime.auth_detail = "manual_approval_required"
                    self._logger.info(
                        "instagram_pv challenge_manual_approval instance=%s", instance
                    )

                def _challenge_resolve_shim(_last_json: dict) -> bool:
                    """Instance-level replacement for instagrapi's guarded
                    ``challenge_resolve``: lets the private_request wrapper
                    auto-resolve checkpoints (Bloks bypass, email/SMS code via
                    our code handler) and then retry the ORIGINAL login request
                    with the identical signed payload — exactly how upstream
                    finishes checkpointed logins."""
                    outcome = _resolve_checkpoint()
                    if outcome == "manual":
                        raise _ManualApprovalPending()
                    return True

                try:
                    client.username = username

                    def _login_once() -> None:
                        if sessionid:
                            client.login_by_sessionid(sessionid)
                            return
                        try:
                            if not client.login(username, password):
                                raise RuntimeError("Instagram login returned false")
                        except TwoFactorRequired:
                            verification_code = str(params.get("instagram_verification_code") or "").strip()
                            totp_seed = str(params.get("instagram_totp_seed") or "").strip()
                            code = verification_code or (_totp_code(totp_seed) if totp_seed else "")
                            if not code:
                                raise
                            if not client.login(username, password, verification_code=code):
                                raise RuntimeError("Instagram two-factor login returned false")
                        except BadPassword:
                            raise RuntimeError("bad_password: Instagram rejected the credentials")

                    client.challenge_resolve = _challenge_resolve_shim

                    max_rounds = 3
                    for round_no in range(1, max_rounds + 1):
                        try:
                            _login_once()
                            break
                        except _ManualApprovalPending:
                            _freeze_manual(
                                "Instagram returned a native checkpoint. It did not issue an "
                                "email/SMS code or an approval notification for this login. Sign in "
                                "on instagram.com from a trusted browser, then update this instance "
                                "with that browser's sessionid and reconnect."
                            )
                            return
                        except ChallengeRequired:
                            # The identical-payload retry was challenged again;
                            # a flagged account can need more than one round.
                            self._logger.info(
                                "instagram_pv challenge_retry_still_challenged instance=%s round=%s",
                                instance,
                                round_no,
                            )
                            time.sleep(2)
                    else:
                        # Even identical-payload retries keep getting challenged:
                        # the programmatic acknowledge closes each checkpoint but
                        # Instagram insists on in-app approval for this account.
                        # Load the current Bloks context WITHOUT consuming it, so
                        # the frozen client stays dismissible after the user
                        # approves the login in the official app.
                        try:
                            api_path = ((client.last_json or {}).get("challenge") or {}).get("api_path")
                            if api_path:
                                freeze_url = client._normalize_challenge_api_path(api_path)
                                freeze_params = {
                                    "guid": client.uuid,
                                    "device_id": client.android_device_id,
                                }
                                freeze_cc = ((client.last_json or {}).get("challenge") or {}).get(
                                    "challenge_context"
                                )
                                if freeze_cc:
                                    freeze_params["challenge_context"] = freeze_cc
                                client._send_private_request(
                                    freeze_url.lstrip("/"), params=freeze_params
                                )
                        except Exception:
                            pass
                        last = client.last_json or {}
                        if last.get("bloks_action") and last.get("challenge_context"):
                            _freeze_manual(
                                "Instagram returned a native checkpoint and did not issue an "
                                "email/SMS code or an approval notification for this login. Sign in "
                                "on instagram.com from a trusted browser, then update this instance "
                                "with that browser's sessionid and reconnect."
                            )
                            return
                        raise RuntimeError(
                            f"checkpoint keeps re-appearing after {max_rounds} resolve+login rounds; "
                            "approve the login in the official Instagram app, then click Reconnect"
                        )
                except Exception as exc:
                    try:
                        client.dump_settings(session_path)
                    except Exception:
                        pass
                    runtime.challenge_state = "failed"
                    runtime.challenge_detail = str(exc)[:300]
                    runtime.authenticated = False
                    runtime.auth_detail = f"challenge_failed: {str(exc)[:200]}"
                    self._logger.warning(
                        "instagram_pv challenge_flow_failed instance=%s error=%s", instance, exc
                    )
                    return

                # Success: adopt the client as the live session.
                try:
                    self._adopt_client(runtime, client, session_path)
                except Exception as exc:
                    runtime.authenticated = False
                    runtime.challenge_state = "failed"
                    runtime.challenge_detail = f"Session verification failed: {type(exc).__name__}"
                    return
                self._logger.info(
                    "instagram_pv challenge_flow_connected instance=%s self_id=%s",
                    instance,
                    runtime.self_user_id,
                )

            asyncio.create_task(asyncio.to_thread(_flow))

            # Wait for the first milestone so the panel gets immediate feedback.
            for _ in range(60):
                await asyncio.sleep(1)
                if runtime.challenge_state in ("code_sent", "failed", "connected", "manual_approval"):
                    break

        return {
            "state": runtime.challenge_state,
            "detail": runtime.challenge_detail,
            "choice": runtime.challenge_choice,
        }

    async def submit_challenge_code(self, instance: str, code: str) -> dict[str, Any]:
        """Submit the emailed/SMSed security code for a pending challenge."""
        runtime = self._runtimes.get(instance)
        if runtime is None or runtime.challenge_state != "code_sent":
            raise ValueError(
                "no checkpoint challenge is waiting for a code; start the challenge first"
            )
        code = str(code or "").strip()
        if not code:
            raise ValueError("empty code")
        runtime.challenge_code = code
        runtime.challenge_state = "resolving"
        runtime.challenge_detail = "submitting security code"
        if runtime.challenge_event is not None:
            runtime.challenge_event.set()

        # Wait for the worker to finish (bounded) so the panel gets the result.
        for _ in range(150):
            await asyncio.sleep(1)
            if runtime.challenge_state in ("connected", "failed", "code_sent"):
                break

        state = await self.get_connection_state(instance)
        state["session_file"] = bool(
            runtime.session_path and runtime.session_path.exists()
        )
        if runtime.challenge_state == "failed":
            state["detail"] = runtime.challenge_detail or state.get("detail")
        elif runtime.challenge_state == "code_sent":
            # Code was rejected and Instagram sent a new one.
            state["detail"] = runtime.challenge_detail
        return state

    async def resume_challenge(self, instance: str) -> dict[str, Any]:
        """Resume a frozen manual-approval checkpoint after the user approved it
        in the official Instagram app.

        Uses instagrapi's ``challenge_bloks_redirect_dismiss`` on the kept
        client (instagrapi PR #2652) so device/session bindings survive, then
        re-runs the login to fetch the final session cookies.
        """
        from instagrapi.exceptions import ChallengeRequired

        runtime = self._runtimes.get(instance)
        client = runtime.challenge_client if runtime else None
        if (
            runtime is None
            or client is None
            or runtime.challenge_state != "manual_approval"
        ):
            raise ValueError(
                "no pending manual-approval checkpoint to resume; start the challenge first"
            )
        params = runtime.params
        username = str(params.get("instagram_username") or "").strip()
        password = str(params.get("instagram_password") or "")
        sessionid = str(params.get("instagram_sessionid") or "").strip()
        if sessionid and "***" in sessionid:
            sessionid = ""
        session_path = runtime.session_path

        runtime.challenge_state = "resolving"
        runtime.challenge_detail = "acknowledging the approved checkpoint"

        if runtime.challenge_native_flow and not sessionid:
            # Native checkpoints do not expose an action that this connector
            # can safely complete. Retrying private challenge endpoints merely
            # creates additional invisible checkpoints.
            runtime.challenge_state = "manual_approval"
            runtime.challenge_detail = (
                "Instagram did not issue an approval request or security code for this "
                "native checkpoint. Update the instance with the sessionid from its trusted "
                "instagram.com browser session, then use Reconnect."
            )
            state = await self.get_connection_state(instance)
            state["session_file"] = bool(session_path and session_path.exists())
            return state

        def _refresh_bloks_context() -> bool:
            """Reload a live Bloks challenge context onto the frozen client.

            Returns True when the account turned out to be unlocked already
            (the probe login succeeded outright).
            """
            if sessionid:
                return bool(client.login_by_sessionid(sessionid))
            try:
                if not client.login(username, password):
                    raise RuntimeError("Instagram login returned false")
                return True
            except ChallengeRequired:
                pass
            api_path = ((client.last_json or {}).get("challenge") or {}).get("api_path")
            if not api_path:
                raise RuntimeError("could not obtain a fresh checkpoint context")
            refresh_url = client._normalize_challenge_api_path(api_path)
            refresh_params = {
                "guid": client.uuid,
                "device_id": client.android_device_id,
            }
            refresh_cc = ((client.last_json or {}).get("challenge") or {}).get("challenge_context")
            if refresh_cc:
                refresh_params["challenge_context"] = refresh_cc
            client._send_private_request(refresh_url.lstrip("/"), params=refresh_params)
            return False

        def _resume() -> None:
            if sessionid:
                # Session-cookie auth cannot hit this checkpoint at all.
                client.login_by_sessionid(sessionid)
                return
            last = client.last_json or {}
            has_context = bool(
                last.get("bloks_action") == "com.bloks.www.ig.challenge.redirect.async"
                and last.get("challenge_context")
            )
            if not has_context:
                # A previous failed attempt consumed the context — reload it.
                if _refresh_bloks_context():
                    return  # account unlocked already; login done
            client.challenge_bloks_redirect_dismiss()
            if not client.login(username, password):
                raise RuntimeError("Instagram login returned false")

        try:
            await asyncio.to_thread(_resume)
            await asyncio.to_thread(self._adopt_client, runtime, client, session_path)
        except Exception as exc:
            # Restore a live context so the next Resume click still works.
            try:
                if await asyncio.to_thread(_refresh_bloks_context):
                    await asyncio.to_thread(self._adopt_client, runtime, client, session_path)
                    state = await self.get_connection_state(instance)
                    state["session_file"] = bool(session_path and session_path.exists())
                    return state
            except Exception:
                pass
            if session_path is not None:
                try:
                    await asyncio.to_thread(client.dump_settings, session_path)
                except Exception:
                    pass
            runtime.challenge_state = "manual_approval"
            runtime.challenge_detail = (
                f"not cleared yet: {str(exc)[:200]} — approve the login in the app first, "
                "then resume again"
            )
            state = await self.get_connection_state(instance)
            state["session_file"] = bool(session_path and session_path.exists())
            return state

        self._logger.info(
            "instagram_pv challenge_resumed_connected instance=%s self_id=%s",
            instance,
            runtime.self_user_id,
        )
        state = await self.get_connection_state(instance)
        state["session_file"] = bool(session_path and session_path.exists())
        return state

    async def check_connectivity(
        self,
        instance: str,
        params: dict[str, Any],
        proxy: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Live connectivity probe for the admin panel "Check" button.

        Ensures the session is initialized (``connect`` is a no-op when the
        runtime is already authenticated), then performs a real API call
        (``account_info``) to verify the session token is still accepted by
        Instagram. A ``LoginRequired`` response marks the runtime as expired
        so subsequent polls re-authenticate.
        """
        from instagrapi.exceptions import LoginRequired

        try:
            await self.connect(instance, params, proxy)
        except Exception as exc:
            runtime = self._runtimes.get(instance)
            return {
                "connected": False,
                "detail": str(exc) or (runtime.auth_detail if runtime else "connect_failed"),
                "username": runtime.self_username if runtime else None,
                "user_id": runtime.self_user_id if runtime else None,
                "session_file": bool(
                    runtime and runtime.session_path and runtime.session_path.exists()
                ),
                "rate_limited": bool(
                    runtime and time.monotonic() < runtime.rate_limited_until
                ),
            }

        runtime = self._get_runtime(instance)
        async with runtime.lock:
            if not (runtime.authenticated and runtime.client is not None):
                return {
                    "connected": False,
                    "detail": runtime.auth_detail,
                    "username": runtime.self_username,
                    "user_id": runtime.self_user_id,
                    "session_file": bool(runtime.session_path and runtime.session_path.exists()),
                    "rate_limited": time.monotonic() < runtime.rate_limited_until,
                }
            try:
                account = await asyncio.to_thread(runtime.client.account_info)
                await asyncio.to_thread(runtime.client.direct_threads, amount=1, thread_message_limit=1)
            except LoginRequired:
                runtime.authenticated = False
                runtime.auth_detail = "login_required: session expired"
                logger.warning("instagram_pv check_connectivity session_expired instance=%s", instance)
            except Exception as exc:
                logger.warning("instagram_pv check_connectivity probe_failed instance=%s error=%s", instance, exc)
                return {
                    "connected": False,
                    "detail": f"probe_failed: {exc}",
                    "username": runtime.self_username,
                    "user_id": runtime.self_user_id,
                    "session_file": bool(runtime.session_path and runtime.session_path.exists()),
                    "rate_limited": time.monotonic() < runtime.rate_limited_until,
                }
            else:
                runtime.self_username = str(getattr(account, "username", "") or "") or runtime.self_username
                runtime.self_user_id = str(getattr(account, "pk", "") or "") or runtime.self_user_id
                runtime.auth_detail = "authenticated"

        return {
            "connected": bool(runtime.authenticated and runtime.client is not None),
            "detail": runtime.auth_detail,
            "username": runtime.self_username,
            "user_id": runtime.self_user_id,
            "session_file": bool(runtime.session_path and runtime.session_path.exists()),
            "rate_limited": time.monotonic() < runtime.rate_limited_until,
        }

    async def close(self) -> None:
        """Persist sessions and drop all runtimes."""
        for runtime in list(self._runtimes.values()):
            try:
                await self._dump_settings(runtime)
                await self._persist_watermarks(runtime)
            except Exception:
                pass
        self._runtimes.clear()


instagram_pv = InstagramPvConnector()
