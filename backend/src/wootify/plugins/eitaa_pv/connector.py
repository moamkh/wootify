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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

from wootify.paths import legacy_or_var, resolve_project_path

logger = logging.getLogger("wootify.plugins.eitaa_pv")
DEFAULT_EITAA_PV_SESSION_DIR = legacy_or_var("data/eitaa_pv_sessions", "sessions/eitaa_pv")


class _StickyEitaaTransport:
    """Keep every part of an Eitaa upload on one upload server.

    Eitaa Web selects a server once for a file-networker.  The upstream client
    shuffled its upload endpoint per RPC, which can split a multi-part file
    across hosts and make finalization fail with INTERNAL_SERVER_ERROR10.
    """

    def __init__(self, settings: Any) -> None:
        self.settings = settings
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
        errors: list[str] = []
        for endpoint in endpoints:
            try:
                response = await self._client.post(endpoint, content=payload)
            except httpx.HTTPError as exc:
                errors.append(f"{endpoint}: {type(exc).__name__}")
                continue
            if response.status_code != httpx.codes.OK or not response.content:
                errors.append(f"{endpoint}: HTTP {response.status_code}")
                continue
            self._selected[kind] = endpoint
            return response.content
        raise EitaaTransportError(f"all Eitaa {kind} endpoints failed: {'; '.join(errors)}")


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
            from eitaa_cli import EitaaClient
            from eitaa_cli.config import EitaaSettings

            session_dir = self._session_dir(params)
            session_file = session_dir / f"{instance}.json"
            settings = EitaaSettings(
                profile=instance,
                session_file=session_file,
                endpoint=str(params.get("eitaa_pv_endpoint") or "").strip() or None,
            )
            client = await EitaaClient.create(
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
        result = await runtime.client.auth.sign_in(
            runtime.challenge.phone_number,
            runtime.challenge.phone_code_hash,
            str(code).strip(),
            profile_name=instance,
        )
        runtime.connected = bool(runtime.client.profile.authenticated)
        return dict(result)

    async def get_connection_state(self, instance: str) -> dict[str, Any]:
        runtime = self._runtimes.get(instance)
        if runtime is None:
            return {"connected": False, "detail": "not_initialized"}
        return {
            "connected": bool(runtime.client.profile.authenticated),
            "detail": "authenticated" if runtime.client.profile.authenticated else "awaiting_auth_code",
            "phone_number": runtime.phone_number,
        }

    def get_self_id(self, instance: str) -> Optional[str]:
        """Return the authenticated Eitaa user id when the session exposes it."""
        runtime = self._runtimes.get(instance)
        if runtime is None:
            return None
        profile = runtime.client.profile
        value = getattr(profile, "user_id", None) or getattr(profile, "id", None)
        return str(value) if value is not None else None

    async def send_text(self, instance: str, chat_id: str, text: str, quoted: Optional[dict[str, Any]] = None, reply_markup: Any = None) -> dict[str, Any]:
        del reply_markup
        runtime = self._authenticated_runtime(instance)
        reply_to = self._message_id(quoted)
        result = await runtime.client.messages.send_text(self._peer_ref(runtime, chat_id), text, reply_to=reply_to)
        return dict(result)

    async def send_media(self, instance: str, chat_id: str, media_url_or_bytes: Any, filename: str, caption: Optional[str] = None, quoted: Optional[dict[str, Any]] = None, reply_markup: Any = None) -> dict[str, Any]:
        del reply_markup
        runtime = self._authenticated_runtime(instance)
        payload = await self._media_path(media_url_or_bytes, filename)
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
            )
            return dict(result)
        finally:
            if payload.parent.parent.name == "wootify-eitaa-upload":
                payload.unlink(missing_ok=True)
                payload.parent.rmdir()

    @staticmethod
    async def _send_media_with_checksum(
        runtime: EitaaPvRuntime,
        peer_reference: str,
        path: Path,
        *,
        content_type: str,
        caption: str,
        reply_to: Optional[int],
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

        peer = await runtime.client.peers.resolve(peer_reference)
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
                    accepted = await runtime.client.invoke("upload.saveBigFilePart", params, kind="upload")
                else:
                    accepted = await runtime.client.invoke("upload.saveFilePart", params, kind="upload")
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
            "random_id": secrets.randbits(63) or 1,
        }
        if reply_to is not None:
            params["reply_to_msg_id"] = reply_to
        return dict(await runtime.client.invoke("messages.sendMedia", params, kind="upload"))

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
        """Poll new messages from active dialogs until upstream exposes a stream API."""
        del offset, timeout
        runtime = self._authenticated_runtime(instance)
        await self._set_presence(runtime, online=True)
        dialogs = await runtime.client.dialogs.list(limit=100)
        entities = self._entity_map(dialogs)
        events: list[dict[str, Any]] = []
        for dialog in dialogs.get("dialogs", []):
            peer = dialog.get("peer") or {}
            chat_id, peer_ref = self._peer_identity(peer, entities)
            if not chat_id:
                continue
            runtime.peer_refs[chat_id] = peer_ref
            history = await runtime.client.messages.history(peer_ref, limit=20)
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

    def _runtime(self, instance: str) -> EitaaPvRuntime:
        runtime = self._runtimes.get(instance)
        if runtime is None:
            raise RuntimeError(f"Eitaa PV instance {instance!r} is not initialized")
        return runtime

    def _authenticated_runtime(self, instance: str) -> EitaaPvRuntime:
        runtime = self._runtime(instance)
        if not runtime.client.profile.authenticated:
            raise RuntimeError("Eitaa PV authentication is required")
        return runtime

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
        return runtime.peer_refs.get(str(chat_id), str(chat_id))

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
