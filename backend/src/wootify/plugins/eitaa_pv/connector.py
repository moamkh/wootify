"""Eitaa personal-account connector backed by Eitaa's TL-over-HTTPS web API."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import mimetypes
import os
import random
import secrets
import tempfile
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx

from wootify.paths import legacy_or_var, resolve_project_path
from wootify.plugins.eitaa_pv.session_client import EitaaLoginRequired, EitaaRefreshPending, SessionAwareEitaaClient

logger = logging.getLogger("wootify.plugins.eitaa_pv")
DEFAULT_EITAA_PV_SESSION_DIR = legacy_or_var("data/eitaa_pv_sessions", "sessions/eitaa_pv")
_FINAL_SEND: ContextVar[bool] = ContextVar("eitaa_final_send", default=False)


class EitaaPreSendError(RuntimeError):
    """A temporary failure before a customer-message send could be accepted."""

    retryable = True
    delivery_uncertain = False


class EitaaSendUnconfirmedError(RuntimeError):
    """The final send may have reached Eitaa; do not blindly repeat it."""

    retryable = False
    delivery_uncertain = True


class EitaaSendRejectedError(RuntimeError):
    """A definitive negative acknowledgement, not a positive delivery receipt."""

    retryable = False
    delivery_uncertain = False
    delivery_rejected = True

    def __init__(self, message: str, *, retryable: bool = False, retry_after_seconds: Optional[int] = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class _StickyEitaaTransport:
    """Keep every part of an Eitaa upload on one upload server.

    Eitaa Web selects a server once for a file-networker.  The upstream client
    shuffled its upload endpoint per RPC, which can split a multi-part file
    across hosts and make finalization fail with INTERNAL_SERVER_ERROR10.
    """

    def __init__(self, settings: Any) -> None:
        from eitaa_cli.tl.codec import TLCodec

        self.settings = settings
        self._codec = TLCodec()
        self._send_method_ids = {
            self._codec.schema.method(method).id & 0xFFFFFFFF
            for method in ("messages.sendMessage", "messages.sendMedia")
        }
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout),
            follow_redirects=False,
            http2=settings.http2,
            headers=settings.web_profile.headers(),
        )
        self._selected: dict[str, str] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def post(self, payload: bytes, *, kind: str = "client") -> bytes:
        from eitaa_cli.errors import EitaaTransportError

        endpoints = list(self.settings.endpoints(kind))
        selected = self._selected.get(kind)
        if selected in endpoints:
            endpoints.remove(selected)
            endpoints.insert(0, selected)
        # Session renewal shares this task with the send. Inspect the known
        # envelope so renewal/read/upload-part failover stays unchanged; only
        # final customer-message writes must not replay after a lost response.
        guarded_send = _FINAL_SEND.get() and self._is_final_send_payload(payload)
        if guarded_send and not endpoints:
            raise EitaaPreSendError("No Eitaa send endpoint configured")
        errors: list[str] = []
        for endpoint in endpoints:
            try:
                response = await self._client.post(endpoint, content=payload)
            except httpx.HTTPError as exc:
                if guarded_send:
                    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
                        raise EitaaPreSendError("Eitaa connection failed before send dispatch") from exc
                    raise EitaaSendUnconfirmedError("Eitaa send transport response was lost") from exc
                errors.append(f"{endpoint}: {type(exc).__name__}")
                continue
            if response.status_code != httpx.codes.OK or not response.content:
                if guarded_send:
                    raise EitaaSendUnconfirmedError(
                        f"Eitaa send acknowledgement missing: HTTP {response.status_code}"
                    )
                errors.append(f"{endpoint}: HTTP {response.status_code}")
                continue
            self._selected[kind] = endpoint
            return response.content
        raise EitaaTransportError(f"all Eitaa {kind} endpoints failed: {'; '.join(errors)}")

    def _is_final_send_payload(self, payload: bytes) -> bool:
        """Identify only captured send methods inside the stateless envelope."""
        try:
            envelope = self._codec.decode_method_request("eitaaObject", payload)
            packed = envelope.get("packed_data")
            return isinstance(packed, bytes) and int.from_bytes(packed[:4], "little") in self._send_method_ids
        except Exception:
            # While a send is active, an uninspectable envelope cannot be
            # assumed replay-safe. Malformed data fails closed.
            return True


@dataclass
class EitaaPvRuntime:
    instance_key: str
    phone_number: str
    session_file: Path
    client: Any
    challenge: Any = None
    connected: bool = False
    peer_refs: dict[str, str] = field(default_factory=dict)
    seen_message_ids: dict[str, int] = field(default_factory=dict)
    # Eitaa exposes an edited message through history with its original id and
    # an updated ``edit_date``.  A high-water message id alone would therefore
    # never surface an edit to the bridge.
    observed_edit_dates: dict[str, dict[int, int]] = field(default_factory=dict)
    pending_read_message_ids: dict[str, int] = field(default_factory=dict)
    read_message_ids: dict[str, int] = field(default_factory=dict)
    presence_online: bool = False
    update_state: Optional[dict[str, Any]] = None
    pending_update_state: Optional[dict[str, Any]] = None
    pending_updates: Optional[list[dict[str, Any]]] = None
    web_poll_interval: Optional[float] = None
    peers_loaded: bool = False
    phone_resolution_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class EitaaPvConnector:
    """Manages one token-backed Eitaa Web session per Wootify instance."""

    def __init__(self) -> None:
        self._runtimes: dict[str, EitaaPvRuntime] = {}
        self._lock = asyncio.Lock()

    async def connect(self, instance: str, params: dict[str, Any], proxy: Optional[dict[str, Any]] = None) -> None:
        del proxy  # The upstream client deliberately uses Eitaa Web's HTTP profile.
        phone = str(params.get("eitaa_pv_phone_number") or "").strip()
        if not phone:
            raise RuntimeError(f"Eitaa PV instance {instance!r} is missing eitaa_pv_phone_number")
        async with self._lock:
            runtime = self._runtimes.get(instance)
            if runtime is not None and runtime.phone_number == phone:
                runtime.connected = bool(runtime.client.profile.authenticated)
                return
            if runtime is not None:
                await runtime.client.close()
            from eitaa_cli.config import EitaaSettings

            session_dir = self._session_dir(params)
            session_file = session_dir / f"{instance}.json"
            settings = EitaaSettings(
                profile=instance,
                session_file=session_file,
                endpoint=str(params.get("eitaa_pv_endpoint") or "").strip() or None,
            )
            client = await SessionAwareEitaaClient.create(
                settings,
                profile=instance,
                require_auth=False,
                transport=_StickyEitaaTransport(settings),
            )
            self._runtimes[instance] = EitaaPvRuntime(
                instance_key=instance,
                phone_number=phone,
                session_file=session_file,
                client=client,
                connected=bool(client.profile.authenticated),
            )

    async def disconnect(self, instance: str) -> None:
        runtime = self._runtimes.pop(instance, None)
        if runtime is not None:
            await self._set_presence(runtime, online=False)
            await runtime.client.close()

    async def close(self) -> None:
        for instance in list(self._runtimes):
            await self.disconnect(instance)

    async def send_auth_code(self, instance: str) -> dict[str, Any]:
        runtime = self._runtime(instance)
        runtime.challenge = await runtime.client.auth.request_code(runtime.phone_number)
        return runtime.challenge.to_dict()

    async def validate_auth_code(self, instance: str, code: str) -> dict[str, Any]:
        runtime = self._runtime(instance)
        if runtime.challenge is None:
            raise RuntimeError("Request an Eitaa login code before validating it")
        await runtime.client.prepare_reauthentication()
        result = await runtime.client.auth.sign_in(
            runtime.challenge.phone_number,
            runtime.challenge.phone_code_hash,
            str(code).strip(),
            profile_name=instance,
        )
        if result.get("_") != "auth.authorization":
            return dict(result)
        runtime.client.reset_session_recovery()
        runtime.connected = bool(runtime.client.profile.authenticated)
        runtime.seen_message_ids.clear()
        runtime.observed_edit_dates.clear()
        runtime.update_state = None
        runtime.pending_update_state = None
        runtime.pending_updates = None
        runtime.peer_refs.clear()
        runtime.peers_loaded = False
        return dict(result)

    async def get_connection_state(self, instance: str) -> dict[str, Any]:
        runtime = self._runtimes.get(instance)
        if runtime is None:
            return {"connected": False, "detail": "not_initialized"}
        return {
            "connected": bool(runtime.client.profile.authenticated) and not getattr(runtime.client, "login_required", False) and not getattr(runtime.client, "refresh_pending", False),
            "detail": "login_required" if getattr(runtime.client, "login_required", False) else ("token_refresh_pending" if getattr(runtime.client, "refresh_pending", False) else ("authenticated" if runtime.client.profile.authenticated else "awaiting_auth_code")),
            "phone_number": runtime.phone_number,
            "poll_interval_seconds": runtime.web_poll_interval,
        }

    def get_self_id(self, instance: str) -> Optional[str]:
        """Return the authenticated Eitaa user id when the session exposes it."""
        runtime = self._runtimes.get(instance)
        if runtime is None:
            return None
        profile = runtime.client.profile
        value = getattr(profile, "user_id", None) or getattr(profile, "id", None)
        return str(value) if value is not None else None

    async def send_text(self, instance: str, chat_id: str, text: str, quoted: Optional[dict[str, Any]] = None, reply_markup: Any = None, *, idempotency_key: Optional[str] = None, before_dispatch: Optional[Callable[[int], Awaitable[None]]] = None) -> dict[str, Any]:
        del reply_markup
        runtime = self._authenticated_runtime(instance)
        reply_to = self._message_id(quoted)
        try:
            peer = await runtime.client.peers.resolve(self._peer_ref(runtime, chat_id))
        except Exception as exc:
            self._raise_pre_send_if_transient(exc)
            raise
        random_id = self._scoped_random_id(instance, chat_id, idempotency_key)
        params = {"peer": peer, "message": text, "random_id": random_id}
        if reply_to is not None:
            params["reply_to_msg_id"] = reply_to
        return await self._invoke_final_send(runtime, "messages.sendMessage", params,
            kind="client", chat_id=chat_id, before_dispatch=before_dispatch)

    async def send_media(self, instance: str, chat_id: str, media_url_or_bytes: Any, filename: str, caption: Optional[str] = None, quoted: Optional[dict[str, Any]] = None, reply_markup: Any = None, *, idempotency_key: Optional[str] = None, before_dispatch: Optional[Callable[[int], Awaitable[None]]] = None) -> dict[str, Any]:
        del reply_markup
        runtime = self._authenticated_runtime(instance)
        try:
            payload = await self._media_path(media_url_or_bytes, filename)
        except Exception as exc:
            self._raise_pre_send_if_transient(exc)
            raise
        try:
            # Uploaded file parts must be finalized on their upload host.
            # Sending their inputFile to a regular client host produces
            # INTERNAL_SERVER_ERROR10 even after every part returned True.
            content_type = mimetypes.guess_type(payload.name)[0] or "application/octet-stream"
            result = await self._send_media_with_checksum(
                runtime,
                self._peer_ref(runtime, chat_id),
                payload,
                content_type=content_type,
                caption=caption or "",
                reply_to=self._message_id(quoted),
                random_id=self._scoped_random_id(instance, chat_id, idempotency_key),
                before_dispatch=before_dispatch,
                chat_id=chat_id,
            )
            return dict(result)
        finally:
            if payload.parent.parent.name == "wootify-eitaa-upload":
                try:
                    payload.unlink(missing_ok=True)
                    payload.parent.rmdir()
                except OSError as exc:
                    # Local housekeeping must never hide an accepted send and
                    # invite an operator to duplicate the attachment.
                    logger.warning("eitaa_upload_temp_cleanup_failed error=%s", type(exc).__name__)

    @staticmethod
    async def _send_media_with_checksum(
        runtime: EitaaPvRuntime,
        peer_reference: str,
        path: Path,
        *,
        content_type: str,
        caption: str,
        reply_to: Optional[int],
        random_id: Optional[int] = None,
        before_dispatch: Optional[Callable[[int], Awaitable[None]]] = None,
        chat_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Upload and send media with an MD5 checksum for small input files."""
        from eitaa_cli.services.peers import input_peer_to_peer

        size = (await asyncio.to_thread(path.stat)).st_size
        if size <= 0:
            raise RuntimeError("Cannot send an empty attachment")
        is_big = size >= 10 * 1024 * 1024
        part_size = 512 * 1024 if size > 64 * 1024 * 1024 else 32 * 1024 if size < 100 * 1024 else 256 * 1024
        parts = max(1, math.ceil(size / part_size))
        if parts > 4000:
            raise RuntimeError(f"Attachment requires {parts} upload parts; Eitaa supports at most 4000")

        try:
            peer = await runtime.client.peers.resolve(peer_reference)
        except Exception as exc:
            EitaaPvConnector._raise_pre_send_if_transient(exc)
            raise
        plain_peer = input_peer_to_peer(peer)
        file_id = secrets.randbits(63) or 1
        checksum = "" if is_big else await asyncio.to_thread(EitaaPvConnector._md5_file, path)
        handle = await asyncio.to_thread(path.open, "rb")
        try:
            for index in range(parts):
                chunk = await asyncio.to_thread(handle.read, part_size)
                params: dict[str, Any] = {
                    "file_id": file_id,
                    "file_part": index,
                    "bytes": chunk,
                    "totalFileSize": size,
                }
                if plain_peer is not None:
                    params["peer"] = plain_peer
                if is_big:
                    params["file_total_parts"] = parts
                    method = "upload.saveBigFilePart"
                else:
                    method = "upload.saveFilePart"
                try:
                    accepted = await runtime.client.invoke(method, params, kind="upload")
                except Exception as exc:
                    EitaaPvConnector._raise_pre_send_if_transient(exc)
                    raise
                if accepted is not True:
                    raise RuntimeError(f"Eitaa rejected attachment part {index}")
        finally:
            await asyncio.to_thread(handle.close)

        uploaded: dict[str, Any] = {
            "_": "inputFileBig" if is_big else "inputFile",
            "id": file_id,
            "parts": parts,
            "name": path.name,
        }
        if not is_big:
            uploaded["md5_checksum"] = checksum
        is_photo = content_type.startswith("image/") and content_type not in {"image/gif", "image/webp"}
        if is_photo:
            media: dict[str, Any] = {"_": "inputMediaUploadedPhoto", "file": uploaded}
        else:
            attributes: list[dict[str, Any]] = [{"_": "documentAttributeFilename", "file_name": path.name}]
            if content_type.startswith("audio/"):
                attributes.append({"_": "documentAttributeAudio", "voice": False, "duration": 0})
            elif content_type.startswith("video/"):
                attributes.append(
                    {"_": "documentAttributeVideo", "supports_streaming": True, "duration": 0, "w": 0, "h": 0}
                )
            media = {
                "_": "inputMediaUploadedDocument",
                "file": uploaded,
                "mime_type": content_type,
                "attributes": attributes,
                "force_file": False,
            }
        params = {
            "peer": peer,
            "media": media,
            "message": caption,
            "random_id": random_id or secrets.randbits(63) or 1,
        }
        if reply_to is not None:
            params["reply_to_msg_id"] = reply_to
        target = chat_id or EitaaPvConnector._chat_id_from_peer(peer)
        return await EitaaPvConnector._invoke_final_send(runtime, "messages.sendMedia", params,
            kind="upload", chat_id=target, before_dispatch=before_dispatch)

    @staticmethod
    def _scoped_random_id(instance: str, chat_id: str, key: Optional[str]) -> int:
        # The workflow already scopes keys as instance:peer:message:part.
        # Keep its established hash so retries of existing batches cannot
        # acquire a different protocol identity after this upgrade.
        del instance, chat_id
        return EitaaPvConnector._send_random_id(key) if key else secrets.randbits(63) or 1

    @staticmethod
    def _raise_pre_send_if_transient(error: Exception) -> None:
        from eitaa_cli.errors import EitaaRPCError, EitaaTransportError
        current = error
        seen = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, (httpx.TransportError, EitaaTransportError, EitaaRefreshPending)):
                raise EitaaPreSendError(f"Eitaa pre-send service temporarily unavailable: {error}") from error
            if isinstance(current, httpx.HTTPStatusError) and (current.response.status_code in (408, 429) or current.response.status_code >= 500):
                raise EitaaPreSendError(f"Eitaa attachment download temporarily failed: HTTP {current.response.status_code}") from error
            if isinstance(current, EitaaRPCError) and current.retryable:
                raise EitaaPreSendError(f"Eitaa pre-send RPC temporarily failed: {current}") from error
            current = current.__cause__

    @staticmethod
    async def _invoke_final_send(runtime: EitaaPvRuntime, method: str, params: dict[str, Any], *,
                                 kind: str, chat_id: str,
                                 before_dispatch: Optional[Callable[[int], Awaitable[None]]]) -> dict[str, Any]:
        from eitaa_cli.errors import EitaaRPCError
        if before_dispatch is not None:
            try:
                await before_dispatch(int(params["random_id"]))
            except Exception as exc:
                raise EitaaPreSendError("Eitaa send checkpoint failed before dispatch") from exc
        token = _FINAL_SEND.set(True)
        try:
            try:
                result = await runtime.client.invoke(method, params, kind=kind)
            except (EitaaPreSendError, EitaaSendUnconfirmedError):
                raise
            except EitaaRefreshPending as exc:
                # Renewal raises only before the business RPC or after an
                # explicit auth rejection; an ambiguous renewal write is not
                # an ambiguous customer-message send.
                raise EitaaPreSendError(str(exc)) from None
            except EitaaLoginRequired as exc:
                raise EitaaSendRejectedError(str(exc)) from None
            except EitaaRPCError as exc:
                if exc.code >= 500 or "TIMEOUT" in exc.normalized_text:
                    raise EitaaSendUnconfirmedError(f"Eitaa send failed without a delivery receipt: {exc}") from exc
                raise EitaaSendRejectedError(str(exc), retryable=exc.code in (420, 429),
                    retry_after_seconds=exc.retry_after_seconds) from exc
            except Exception as exc:
                raise EitaaSendUnconfirmedError(f"Eitaa send acknowledgement unavailable: {exc}") from exc
        finally:
            _FINAL_SEND.reset(token)
        return EitaaPvConnector._confirmed_send_result(result, chat_id, int(params["random_id"]))

    @staticmethod
    def _chat_id_from_peer(peer: dict[str, Any]) -> str:
        if not isinstance(peer, dict):
            return ""
        kind = peer.get("_")
        if kind in {"peerUser", "inputPeerUser"}:
            return str(peer.get("user_id") or "")
        if kind in {"peerChat", "inputPeerChat"}:
            return f"chat:{peer.get('chat_id')}"
        if kind in {"peerChannel", "inputPeerChannel"}:
            return f"channel:{peer.get('channel_id')}"
        return ""

    @staticmethod
    def _confirmed_send_result(response: Any, chat_id: str, random_id: Optional[int]) -> dict[str, Any]:
        """Select a positive ACK for this request, never an unrelated update."""
        if not isinstance(response, dict):
            raise EitaaSendUnconfirmedError("eitaa_send_not_acknowledged: response is not an object")
        positive_id = lambda value: isinstance(value, int) and not isinstance(value, bool) and value > 0
        message_id = None
        if response.get("_") == "updateShortSentMessage" and positive_id(response.get("id")):
            message_id = response["id"]
        elif response.get("_") in {"updates", "updatesCombined"}:
            matched, new_messages = set(), set()
            has_message_mapping = False
            updates = response.get("updates") or []
            if not isinstance(updates, list):
                raise EitaaSendUnconfirmedError("eitaa_send_not_acknowledged: invalid update list")
            for update in updates:
                if not isinstance(update, dict):
                    continue
                if update.get("_") == "updateMessageID":
                    has_message_mapping = True
                    if random_id is not None and update.get("random_id") == random_id and positive_id(update.get("id")):
                        matched.add(update["id"])
                if update.get("_") in {"updateNewMessage", "updateNewChannelMessage"}:
                    message = update.get("message") or {}
                    if isinstance(message, dict) and bool(message.get("out") or (message.get("pFlags") or {}).get("out")) and EitaaPvConnector._chat_id_from_peer(message.get("peer_id") or {}) == str(chat_id) and positive_id(message.get("id")):
                        new_messages.add(message["id"])
            candidates = matched if has_message_mapping else new_messages
            if len(candidates) == 1:
                message_id = next(iter(candidates))
        if message_id is None:
            raise EitaaSendUnconfirmedError(f"eitaa_send_not_acknowledged: {response.get('_', 'unknown response')}")
        return {**response, "message_id": str(message_id), "random_id": random_id,
                "delivery_confirmed": True, "confirmation_kind": "eitaa_server_message_id"}

    @staticmethod
    def _send_random_id(key: str) -> int:
        return (int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") & ((1 << 63) - 1)) or 1

    @staticmethod
    def _md5_file(path: Path) -> str:
        digest = hashlib.md5()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    async def update_message(self, instance: str, chat_id: str, message_id: str, text: str) -> dict[str, Any]:
        runtime = self._authenticated_runtime(instance)
        return dict(await runtime.client.messages.edit(self._peer_ref(runtime, chat_id), int(message_id), text))

    async def delete_message(self, instance: str, chat_id: str, message_id: str) -> dict[str, Any]:
        runtime = self._authenticated_runtime(instance)
        return dict(await runtime.client.messages.delete([int(message_id)], peer_reference=self._peer_ref(runtime, chat_id)))

    async def prepare_outbound(self, instance: str, chat_id: str) -> None:
        """Read pending history, then show a short human-like typing indicator."""
        runtime = self._authenticated_runtime(instance)
        await self._mark_history_read(runtime, chat_id)
        peer = await runtime.client.peers.resolve(self._peer_ref(runtime, chat_id))
        await runtime.client.invoke(
            "messages.setTyping",
            {"peer": peer, "action": {"_": "sendMessageTypingAction"}},
        )
        await asyncio.sleep(random.uniform(1.0, 3.0))

    async def finish_outbound(self, instance: str, chat_id: str) -> None:
        """Clear the transient typing indicator after an outbound send attempt."""
        runtime = self._authenticated_runtime(instance)
        peer = await runtime.client.peers.resolve(self._peer_ref(runtime, chat_id))
        await runtime.client.invoke(
            "messages.setTyping",
            {"peer": peer, "action": {"_": "sendMessageCancelAction"}},
        )

    async def download_message_media(
        self,
        instance: str,
        chat_id: str,
        message_id: str,
        filename: str = "attachment",
        content_type: Optional[str] = None,
    ) -> tuple[bytes, str, str]:
        """Download media attached to a message through the authenticated session."""
        runtime = self._authenticated_runtime(instance)
        suffix = Path(filename).suffix or ".bin"
        descriptor, temp_name = tempfile.mkstemp(prefix="wootify-eitaa-", suffix=suffix)
        os.close(descriptor)
        destination = Path(temp_name)
        downloaded_path: Optional[Path] = None
        try:
            downloaded = await runtime.client.media.download_message(
                self._peer_ref(runtime, chat_id), int(message_id), destination
            )
            downloaded_path = Path(downloaded or destination)
            content = await asyncio.to_thread(downloaded_path.read_bytes)
            resolved_name = downloaded_path.name if downloaded_path.name and downloaded_path.name != destination.name else filename
            return content, resolved_name, content_type or mimetypes.guess_type(resolved_name)[0] or "application/octet-stream"
        finally:
            destination.unlink(missing_ok=True)
            if downloaded_path is not None and downloaded_path != destination:
                downloaded_path.unlink(missing_ok=True)

    async def get_updates(self, instance: str, offset: Optional[int] = None, timeout: Optional[int] = None) -> dict[str, Any]:
        """Read Eitaa Web's difference stream; acknowledge only after durable enqueue."""
        del offset, timeout
        runtime = self._authenticated_runtime(instance)
        if runtime.pending_updates is not None:
            return {"ok": True, "result": runtime.pending_updates}
        await self._set_presence(runtime, online=True)
        if not runtime.peers_loaded:
            dialogs = await runtime.client.dialogs.list(limit=100)
            self._check_response(runtime, dialogs)
            entities = self._entity_map(dialogs)
            for dialog in dialogs.get("dialogs") or []:
                chat_id, peer_ref = self._peer_identity(dialog.get("peer") or {}, entities)
                if chat_id:
                    runtime.peer_refs[chat_id] = peer_ref
            runtime.peers_loaded = True
        if runtime.web_poll_interval is None:
            config = await runtime.client.invoke("help.getConfig", {})
            self._check_expiry(runtime, config)
            runtime.web_poll_interval = max(1.0, float(config.get("schedule_period_foreground_ms") or 5000) / 1000)
        if runtime.update_state is None:
            state = await runtime.client.invoke("updates.getState", {})
            self._check_expiry(runtime, state)
            if state.get("_") != "updates.state":
                raise RuntimeError(f"Unexpected Eitaa update state: {state.get('_')}")
            # Initial login starts at the current state, not arbitrary old history.
            runtime.pending_update_state = self._state_cursor(state)
            runtime.pending_updates = []
            return {"ok": True, "result": []}
        state = runtime.update_state
        difference = await runtime.client.invoke("updates.getDifference", {
            "pts": state["pts"], "date": state["date"], "qts": -1,
        })
        self._check_expiry(runtime, difference)
        kind = difference.get("_")
        if kind == "updates.differenceTooLong":
            # Never silently reset the cursor and discard unreceived messages.
            raise RuntimeError("eitaa_difference_too_long: history recovery required")
        if kind not in {"updates.differenceEmpty", "updates.difference", "updates.differenceSlice"}:
            raise RuntimeError(f"Unexpected Eitaa update difference: {kind}")
        entities = self._entity_map(difference)
        events: list[dict[str, Any]] = []
        deletions: list[dict[str, Any]] = []
        messages = list(difference.get("new_messages") or [])
        confirmations: list[dict[str, Any]] = []
        for update in difference.get("other_updates") or []:
            update_kind = update.get("_")
            if update_kind == "updateMessageID":
                request_id, message_id = update.get("random_id"), update.get("id")
                if all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in (request_id, message_id)):
                    confirmations.append({"delivery_confirmation": True, "request_id": str(request_id),
                        "message_id": str(message_id), "platform_message_id": str(message_id),
                        "outgoing": True, "chat_id": "",
                        "update_id": f"eitaa-send-confirmation:{request_id}:{message_id}"})
            elif update_kind in {"updateNewMessage", "updateNewChannelMessage", "updateEditMessage", "updateEditChannelMessage"}:
                messages.append(update.get("message") or {})
            elif update_kind in {"updateDeleteMessages", "updateDeleteChannelMessages"}:
                chat_id = f"channel:{update['channel_id']}" if update.get("channel_id") else ""
                for mid in update.get("messages") or []:
                    deletions.append({"deleted": True, "message_id": str(mid), "chat_id": chat_id,
                                   "update_id": f"{chat_id}:{mid}:delete:{update.get('pts', '')}"})
            elif update_kind == "updateChannelTooLong":
                # Preserve this gap for explicit per-channel recovery instead
                # of silently pretending all channel messages were received.
                channel = f"channel:{update['channel_id']}"
                events.append({"channel_gap": True, "chat_id": channel,
                               "update_id": f"{channel}:gap:{update.get('pts', '')}"})
        encoded_entities = [{"kind": key[0], "id": key[1], "entity": entity} for key, entity in entities.items()]
        for message in messages:
            peer = message.get("peer_id") or {}
            chat_id, peer_ref = self._peer_identity(peer, entities)
            if not chat_id or not message.get("id"):
                continue
            if peer_ref.endswith(":0") and chat_id in runtime.peer_refs:
                peer_ref = runtime.peer_refs[chat_id]
            runtime.peer_refs[chat_id] = peer_ref
            if not bool(message.get("out") or (message.get("pFlags") or {}).get("out")):
                runtime.pending_read_message_ids[chat_id] = max(runtime.pending_read_message_ids.get(chat_id, 0), int(message["id"]))
            import json
            edit_suffix = (
                ":" + hashlib.sha256(json.dumps(message, sort_keys=True, default=str).encode()).hexdigest()[:16]
                if message.get("edit_date") else ""
            )
            events.append({"chat_id": chat_id, "peer": peer, "peer_ref": peer_ref,
                           "message": message, "entities": encoded_entities,
                           "update_id": f"{chat_id}:{message['id']}:{message.get('edit_date') or 0}{edit_suffix}"})
        for confirmation in confirmations:
            peers = {event["chat_id"] for event in events if isinstance(event.get("message"), dict)
                and str(event["message"].get("id")) == confirmation["message_id"]
                and bool(event["message"].get("out") or (event["message"].get("pFlags") or {}).get("out"))}
            if len(peers) == 1:
                confirmation["chat_id"] = peers.pop()
        # Correlation receipts must be processed before their native echoes,
        # otherwise a lost send ACK could create a duplicate Chatwoot bubble.
        events = confirmations + events
        events.extend(deletions)
        # A delete in the same difference as its original needs to share its
        # delivery stream, otherwise an attachment failure could let it overtake.
        peer_by_message = {str(event["message"]["id"]): event["chat_id"] for event in events if event.get("message") and not event["chat_id"].startswith("channel:")}
        for deletion in deletions:
            if not deletion["chat_id"] and deletion["message_id"] in peer_by_message:
                deletion["chat_id"] = peer_by_message[deletion["message_id"]]
        next_state = difference.get("state") or difference.get("intermediate_state")
        runtime.pending_update_state = self._state_cursor(next_state) if next_state else {**state, "date": difference.get("date", state["date"]), "seq": difference.get("seq", state.get("seq", 0))}
        runtime.pending_updates = events
        return {"ok": True, "result": events}

    @staticmethod
    def _state_cursor(state: dict[str, Any]) -> dict[str, Any]:
        return {key: int(state.get(key) or 0) for key in ("pts", "date", "qts", "seq")}

    def restore_update_state(self, instance: str, state: Optional[str]) -> None:
        import json
        runtime = self._runtime(instance)
        if runtime.update_state is None and runtime.pending_updates is None and state:
            try:
                parsed = json.loads(state)
                if isinstance(parsed, dict) and "pts" in parsed and "date" in parsed:
                    runtime.update_state = self._state_cursor(parsed)
            except (ValueError, TypeError):
                pass  # Older history pollers had no usable difference cursor.

    def pending_cursor(self, instance: str) -> Optional[str]:
        import json
        state = self._runtime(instance).pending_update_state
        return json.dumps(state, separators=(",", ":")) if state is not None else None

    def acknowledge_updates(self, instance: str) -> None:
        runtime = self._runtime(instance)
        if runtime.pending_update_state is not None:
            runtime.update_state = runtime.pending_update_state
        runtime.pending_update_state = None
        runtime.pending_updates = None

    def poll_interval(self, instance: str) -> float:
        return self._runtime(instance).web_poll_interval or 5.0

    @staticmethod
    def _check_expiry(runtime: EitaaPvRuntime, response: dict[str, Any]) -> None:
        if response.get("_") in {"eitaa_updates_expire_token", "eitaa_token_updating"}:
            # The session-aware client normally intercepts this. Never turn a
            # recoverable rotation notification into a permanent logout latch.
            raise EitaaRefreshPending("Eitaa token renewal pending; retrying later.")

    async def _get_history_updates(self, instance: str, offset: Optional[int] = None, timeout: Optional[int] = None) -> dict[str, Any]:
        """Poll new messages from active dialogs until upstream exposes a stream API."""
        del offset, timeout
        runtime = self._authenticated_runtime(instance)
        await self._set_presence(runtime, online=True)
        dialogs = await runtime.client.dialogs.list(limit=100)
        self._check_response(runtime, dialogs)
        entities = self._entity_map(dialogs)
        events: list[dict[str, Any]] = []
        for dialog in dialogs.get("dialogs", []):
            peer = dialog.get("peer") or {}
            chat_id, peer_ref = self._peer_identity(peer, entities)
            if not chat_id:
                continue
            runtime.peer_refs[chat_id] = peer_ref
            history = await runtime.client.messages.history(peer_ref, limit=20)
            self._check_response(runtime, history)
            messages = list(history.get("messages") or [])
            newest = max((int(message.get("id") or 0) for message in messages), default=0)
            previous = runtime.seen_message_ids.get(chat_id)
            edit_dates = runtime.observed_edit_dates.setdefault(chat_id, {})
            if previous is None:
                # Establish both watermarks together.  This avoids replaying
                # existing messages (including ones edited before Wootify
                # started) while letting future changes to their edit_date
                # become normal bridge events.
                runtime.seen_message_ids[chat_id] = newest
                for message in messages:
                    message_id = int(message.get("id") or 0)
                    if message_id:
                        edit_dates[message_id] = int(message.get("edit_date") or 0)
                continue  # Do not replay existing history on a fresh process.
            for message in sorted(messages, key=lambda value: int(value.get("id") or 0)):
                message_id = int(message.get("id") or 0)
                if not message_id:
                    continue
                edit_date = int(message.get("edit_date") or 0)
                is_new = message_id > previous
                previous_edit_date = edit_dates.get(message_id)
                # A message outside a prior history window has no local edit
                # watermark.  Learn it first instead of incorrectly replaying
                # an old edit as a fresh Chatwoot update.
                is_edited = (
                    not is_new
                    and previous_edit_date is not None
                    and edit_date > previous_edit_date
                )
                edit_dates[message_id] = max(previous_edit_date or 0, edit_date)
                if is_new or is_edited:
                    if not bool(message.get("out") or (message.get("pFlags") or {}).get("out")):
                        runtime.pending_read_message_ids[chat_id] = max(
                            runtime.pending_read_message_ids.get(chat_id, 0), message_id
                        )
                    # The dialog peer identifies the *other* participant in a
                    # private chat.  Preserve it so normalization never uses
                    # an outgoing message's ``from_id`` (the authenticated
                    # account) as the Chatwoot contact identity.
                    events.append({
                        "chat_id": chat_id,
                        "peer": peer,
                        "peer_ref": peer_ref,
                        "message": message,
                        "entities": entities,
                    })
            runtime.seen_message_ids[chat_id] = max(previous, newest)
        return {"ok": True, "result": events}

    async def resolve_phone_to_user(self, instance: str, phone_number: str) -> dict[str, Any]:
        """Resolve within this account; import one phone only after read-only lookup."""
        from wootify.application.messaging.payload_parser import ChatwootPayloadParser

        runtime = self._authenticated_runtime(instance)
        normalized = ChatwootPayloadParser.normalize_bale_pv_phone(phone_number)
        if not normalized.isascii() or not normalized.isdigit() or not 8 <= len(normalized) <= 15:
            raise RuntimeError("eitaa_invalid_phone_number")
        # Serialize imports for one account. Another resolver can then observe
        # the newly saved contact instead of importing it concurrently.
        async with runtime.phone_resolution_lock:
            contacts = await runtime.client.invoke("contacts.getContacts", {"hash": 0})
            self._check_expiry(runtime, contacts)
            if not isinstance(contacts.get("users"), list):
                raise EitaaPreSendError("eitaa_contacts_lookup_incomplete: retry contact resolution")
            user = self._matching_phone_user(contacts, normalized)
            if user is None:
                user = await self._find_phone_in_dialogs(runtime, normalized)
            if user is None:
                # getContacts/getDialogs only expose known/visible users. An
                # empty search is NOT proof that this phone is unregistered.
                # Import only the requested phone, correlated by our client_id.
                client_id = secrets.randbelow((1 << 63) - 1) + 1
                imported = await runtime.client.invoke("contacts.importContacts", {"contacts": [{
                    "_": "inputPhoneContact", "client_id": client_id,
                    "phone": "+" + normalized, "first_name": "Eitaa contact", "last_name": "",
                }]})
                self._check_expiry(runtime, imported)
                if str(client_id) in {str(value) for value in imported.get("retry_contacts") or []}:
                    raise EitaaPreSendError("eitaa_phone_import_retry_requested: Eitaa asked to retry the lookup")
                ids = {str(item.get("user_id")) for item in imported.get("imported") or []
                       if str(item.get("client_id")) == str(client_id) and item.get("user_id")}
                if len(ids) > 1:
                    raise RuntimeError("eitaa_phone_ambiguous")
                candidates = [item for item in imported.get("users") or [] if str(item.get("id")) in ids]
                if candidates:
                    # The explicit imported client_id -> user_id association is
                    # valid even when privacy hides the returned phone field.
                    if any(item.get("phone") and ChatwootPayloadParser.normalize_bale_pv_phone(
                            str(item["phone"])) != normalized for item in candidates):
                        raise RuntimeError("eitaa_phone_import_identity_mismatch")
                    if any(item != candidates[0] for item in candidates[1:]):
                        raise RuntimeError("eitaa_phone_ambiguous")
                    if self._has_user_access_hash(candidates[0]):
                        user = candidates[0]
                if user is None:
                    raise RuntimeError("eitaa_phone_unresolved_for_account: Eitaa did not return a verified "
                                       "recipient for this account; check phone discoverability or ask the "
                                       "customer to message this Eitaa account first")
            runtime.peer_refs[str(user["id"])] = f"user:{user['id']}:{user['access_hash']}"
            return user

    @staticmethod
    def _has_user_access_hash(user: dict[str, Any]) -> bool:
        return (str(user.get("id") or "").isdigit() and int(user["id"]) > 0
                and not isinstance(user.get("access_hash"), bool)
                and str(user.get("access_hash") or "").lstrip("-").isdigit()
                and int(user["access_hash"]) != 0)

    @classmethod
    def _matching_phone_user(cls, response: dict[str, Any], normalized: str) -> Optional[dict[str, Any]]:
        from wootify.application.messaging.payload_parser import ChatwootPayloadParser

        matches = {
            str(user["id"]): user
            for user in response.get("users") or []
            if user.get("id")
            and ChatwootPayloadParser.normalize_bale_pv_phone(str(user.get("phone") or "")) == normalized
        }
        if len(matches) > 1:
            raise RuntimeError("eitaa_phone_ambiguous")
        user = next(iter(matches.values()), None)
        return user if user is not None and cls._has_user_access_hash(user) else None

    async def _find_phone_in_dialogs(self, runtime: EitaaPvRuntime, normalized: str) -> Optional[dict[str, Any]]:
        """Page known dialogs without generic search or first-hit routing.

        Bound the read-only scan; a missing cursor or capped scan still falls
        back to targeted import, never a claim that the phone is unregistered.
        """
        response = await runtime.client.dialogs.list(limit=100)
        cursors: set[tuple[int, int, str]] = set()
        for page in range(20):
            self._check_expiry(runtime, response)
            if not isinstance(response.get("dialogs"), list) or not isinstance(response.get("users"), list):
                raise EitaaPreSendError("eitaa_dialog_lookup_incomplete: retry contact resolution")
            user = self._matching_phone_user(response, normalized)
            if user is not None:
                return user
            dialogs = response["dialogs"]
            if not dialogs or (response.get("_") != "messages.dialogsSlice" and len(dialogs) < 100):
                break
            last = dialogs[-1]
            peer = last.get("peer") or {}
            top_id = int(last.get("top_message") or 0)
            messages = [item for item in response.get("messages") or []
                        if int(item.get("id") or 0) == top_id
                        and (not item.get("peer_id") or item["peer_id"] == peer)]
            date = int(messages[0].get("date") or 0) if len(messages) == 1 else 0
            kind = peer.get("_")
            entity_type = "user" if kind == "peerUser" else "channel"
            identifier = peer.get("user_id") if kind == "peerUser" else peer.get("channel_id")
            entity = self._entity_map(response).get((entity_type, int(identifier or 0)), {})
            if kind == "peerChat" and str(peer.get("chat_id") or "").isdigit() and int(peer["chat_id"]) > 0:
                input_peer = {"_": "inputPeerChat", "chat_id": peer.get("chat_id")}
            elif kind in {"peerUser", "peerChannel"} and self._has_user_access_hash(entity):
                input_peer = {"_": "inputPeerUser" if kind == "peerUser" else "inputPeerChannel",
                              "user_id" if kind == "peerUser" else "channel_id": identifier,
                              "access_hash": entity["access_hash"]}
            else:
                break
            cursor = (top_id, date, str(peer))
            if not top_id or not date or cursor in cursors:
                break
            cursors.add(cursor)
            if page == 19:
                break
            response = await runtime.client.invoke("messages.getDialogs", {
                "exclude_pinned": True, "offset_date": date, "offset_id": top_id,
                "offset_peer": input_peer, "limit": 100, "hash": 0,
            })
        return None

    def _runtime(self, instance: str) -> EitaaPvRuntime:
        runtime = self._runtimes.get(instance)
        if runtime is None:
            raise RuntimeError(f"Eitaa PV instance {instance!r} is not initialized")
        return runtime

    def _authenticated_runtime(self, instance: str) -> EitaaPvRuntime:
        runtime = self._runtime(instance)
        if getattr(runtime.client, "login_required", False) is True:
            raise EitaaLoginRequired("Eitaa login required: server rejected session renewal. Request a new phone login code.")
        if not runtime.client.profile.authenticated:
            raise RuntimeError("Eitaa PV authentication is required")
        return runtime

    @staticmethod
    def _check_response(runtime: EitaaPvRuntime, response: dict[str, Any]) -> None:
        EitaaPvConnector._check_expiry(runtime, response)
        if not any(key in response for key in ("dialogs", "messages")):
            raise RuntimeError(f"Unexpected Eitaa polling response: {response.get('_', 'unknown')}")

    @staticmethod
    def _message_id(quoted: Optional[dict[str, Any]]) -> Optional[int]:
        value = (quoted or {}).get("message_id")
        return int(value) if str(value or "").isdigit() else None

    @staticmethod
    def _entity_map(dialogs: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
        result: dict[tuple[str, int], dict[str, Any]] = {}
        for user in dialogs.get("users") or []:
            result[("user", int(user.get("id") or 0))] = user
        for chat in dialogs.get("chats") or []:
            result[("channel" if chat.get("_") in {"channel", "channelForbidden"} else "chat", int(chat.get("id") or 0))] = chat
        return result

    @staticmethod
    def _peer_identity(peer: dict[str, Any], entities: dict[tuple[str, int], dict[str, Any]]) -> tuple[str, str]:
        kind = str(peer.get("_") or "")
        if kind == "peerUser":
            identifier = int(peer.get("user_id") or 0); entity = entities.get(("user", identifier), {})
            return str(identifier), f"user:{identifier}:{int(entity.get('access_hash') or 0)}"
        if kind == "peerChat":
            identifier = int(peer.get("chat_id") or 0); return f"chat:{identifier}", f"chat:{identifier}"
        if kind == "peerChannel":
            identifier = int(peer.get("channel_id") or 0); entity = entities.get(("channel", identifier), {})
            return f"channel:{identifier}", f"channel:{identifier}:{int(entity.get('access_hash') or 0)}"
        return "", ""

    @staticmethod
    def _peer_ref(runtime: EitaaPvRuntime, chat_id: str) -> str:
        reference = runtime.peer_refs.get(str(chat_id))
        if reference:
            return reference
        if str(chat_id).isdigit() or str(chat_id).startswith("channel:"):
            # The upstream generic resolver chooses the first search hit for
            # a bare numeric ID. Never use that as a routing fallback.
            raise RuntimeError("eitaa_peer_access_hash_unavailable: refresh dialogs or resolve the contact phone")
        return str(chat_id)

    async def _set_presence(self, runtime: EitaaPvRuntime, *, online: bool) -> None:
        if runtime.presence_online == online:
            return
        try:
            await runtime.client.invoke("account.updateStatus", {"offline": not online})
            runtime.presence_online = online
        except Exception as exc:
            logger.debug("eitaa_pv_presence_update_failed instance=%s online=%s error=%s", runtime.instance_key, online, exc)

    async def _mark_history_read(self, runtime: EitaaPvRuntime, chat_id: str) -> None:
        peer_ref = self._peer_ref(runtime, chat_id)
        latest = runtime.pending_read_message_ids.get(chat_id, 0)
        # A new process has no in-memory watermark; query the current dialog
        # head once so all earlier unread messages are acknowledged together.
        if not latest:
            history = await runtime.client.messages.history(peer_ref, limit=1)
            latest = max((int(message.get("id") or 0) for message in history.get("messages") or []), default=0)
        if not latest or runtime.read_message_ids.get(chat_id, 0) >= latest:
            return
        peer = await runtime.client.peers.resolve(peer_ref)
        method = "channels.readHistory" if peer.get("_") == "inputPeerChannel" else "messages.readHistory"
        params = {"channel": peer, "max_id": latest} if method == "channels.readHistory" else {"peer": peer, "max_id": latest}
        await runtime.client.invoke(method, params)
        runtime.read_message_ids[chat_id] = latest

    @staticmethod
    def _session_dir(params: dict[str, Any]) -> Path:
        configured = str(params.get("eitaa_pv_session_dir") or DEFAULT_EITAA_PV_SESSION_DIR)
        path = resolve_project_path(configured)
        if ".." in Path(configured).parts:
            raise RuntimeError("eitaa_pv_session_dir must not contain '..'")
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    async def _media_path(payload: Any, filename: str) -> Path:
        if isinstance(payload, (bytes, bytearray)):
            directory = Path("var/tmp/wootify-eitaa-upload").resolve()
            directory.mkdir(parents=True, exist_ok=True)
            # A Chatwoot webhook can deliver multiple attachments with the
            # same filename concurrently.  Give each upload its own directory
            # while retaining the original filename that Eitaa shows to the
            # recipient.
            upload_dir = Path(tempfile.mkdtemp(prefix="upload-", dir=directory))
            safe_name = Path(filename).name or "file"
            path = upload_dir / safe_name
            await asyncio.to_thread(path.write_bytes, bytes(payload))
            return path
        if isinstance(payload, str) and payload.startswith(("http://", "https://")):
            import httpx
            # Attachment URLs are a safe GET, so retrying an interrupted
            # response cannot duplicate a message.  PDFs tend to be much
            # larger than images and are where a proxy/socket reset normally
            # surfaces as httpx.ReadError.
            timeout = httpx.Timeout(connect=15.0, read=180.0, write=60.0, pool=15.0)
            transient_errors = (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadError,
                httpx.ReadTimeout,
                httpx.RemoteProtocolError,
            )
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(
                        follow_redirects=True,
                        timeout=timeout,
                        # Chatwoot attachment URLs commonly redirect from
                        # ``/blobs/redirect`` to local Active Storage.  Never
                        # route localhost through a desktop/system proxy.
                        trust_env=False,
                        headers={"User-Agent": "Wootify Eitaa PV/1.0"},
                    ) as client:
                        response = await client.get(payload)
                        response.raise_for_status()
                        return await EitaaPvConnector._media_path(response.content, filename)
                except transient_errors as exc:
                    if attempt == 2:
                        raise RuntimeError(
                            "Could not download the Chatwoot attachment after 3 attempts"
                        ) from exc
                    delay = 2 ** attempt
                    logger.warning(
                        "eitaa_pv_attachment_download_retry attempt=%s wait=%ss error=%s",
                        attempt + 1,
                        delay,
                        type(exc).__name__,
                    )
                    await asyncio.sleep(delay)
        raise RuntimeError("Eitaa PV needs bytes or an HTTP(S) media URL")


eitaa_pv = EitaaPvConnector()
