"""Bale PV (personal-account / userbot) connector.

Implements a direct gRPC-Web-over-WebSocket client for Bale Messenger that
operates on behalf of a real phone-number account rather than a bot token.

Architecture
------------
``BalePvConnector`` (singleton ``bale_pv``) manages one
``BalePvInstanceRuntime`` per instance key.  Each runtime owns:

* A ``BaleMessagingClient`` / ``BaleWebSocketClient`` for the gRPC-Web transport.
* An ``asyncio.Queue`` that receives parsed updates from the WebSocket listener.
* Session state persisted to ``data/bale_pv_sessions/`` as JSON files.

Authentication flow
-------------------
1. ``connect()`` — loads an existing session or starts fresh auth.
2. ``send_auth_code()`` — triggers Bale's SMS OTP via ``StartPhoneAuth``.
3. ``validate_auth_code()`` — submits the OTP, receives a JWT, persists it.
4. The JWT is attached to every subsequent WebSocket/gRPC request as a cookie.

Media handling
--------------
Outbound files are uploaded to Bale's Nasim S3-compatible store via
``UploadNasimFile`` before the message is sent.  Inbound files are referenced
by a composite ``file_id`` JSON blob and downloaded on demand via
``GetNasimFileUrl`` / ``GetNasimFileUrls``.

Sticker notes
-------------
Bale sends dedicated stickers as ``StickerMessage`` (proto field 12 of Message G)
with ``mime_type="image/webp"`` and a ``sticker<id>.webp`` filename set by the
parser.  Legacy document-style stickers (proto field 4) may arrive with
``mime_type="image/jpeg"`` and a ``sticker*.png`` filename; these are detected
by filename prefix and re-tagged as ``image/webp`` before forwarding to Chatwoot.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sys

from app.config import settings
from app.utils.logging_utils import redact_secret, truncate_text

# Add the bale_grpc_client package to sys.path so it can be imported without
# being installed as an editable package in every deployment environment.
_bale_grpc_client_path = str(Path(__file__).resolve().parent.parent.parent / "bale_grpc_client")
if _bale_grpc_client_path not in sys.path:
    sys.path.insert(0, _bale_grpc_client_path)

logger = logging.getLogger("app.connectors.bale_pv")


# ---------------------------------------------------------------------------
# Lazy importers — deferred to avoid heavy gRPC dependencies at module load
# and to prevent circular-import issues during application startup.
# ---------------------------------------------------------------------------

def _get_auth_client():
    """Return ``(BaleAuthClient, BaleAuthError)`` from the grpc package."""
    from bale_grpc_client.auth_client import BaleAuthClient
    from bale_grpc_client.exceptions import BaleAuthError
    return BaleAuthClient, BaleAuthError


def _get_messaging_client():
    """Return ``BaleMessagingClient`` from the grpc package."""
    from bale_grpc_client.messaging_client import BaleMessagingClient
    return BaleMessagingClient


def _get_dialog_parser():
    """Return ``parse_import_contacts_response`` from the dialog parser."""
    from bale_grpc_client.dialog_parser import parse_import_contacts_response
    return parse_import_contacts_response


@dataclass
class BalePvInstanceRuntime:
    """In-memory state for a single Bale PV (userbot) instance.

    One runtime exists per ``instance_key`` and is created by
    ``BalePvConnector.connect()``.  Fields are mutated as the session
    progresses through authentication and normal operation.

    Attributes:
        instance_key: Unique identifier for this Wootify instance.
        phone_number: Bale account phone number (E.164, without leading +).
        client: Active ``BaleMessagingClient``; ``None`` until connected.
        message_queue: Parsed update dicts pushed by the WS listener task.
        ws_task: Background ``asyncio.Task`` running the WebSocket listener.
        stop_event: Signals the WS listener to shut down gracefully.
        session_dir: Directory where JWT session files are persisted.
        auth_state: One of ``"unauthenticated"``, ``"code_sent"``,
            ``"authenticated"``.
        transaction_hash: Opaque token returned by ``StartPhoneAuth``,
            required for ``ValidateCode``.
        session_id: UUID that scopes the session file on disk (allows
            multiple concurrent sessions for the same phone number).
        user_cache: Maps Bale user-id → display name; populated lazily.
        chat_title_cache: Maps peer_id → group/channel title.
        self_user_id: The authenticated account's Bale user-id, extracted
            from the JWT payload after login.
        last_user_cache_refresh: Unix timestamp of the most recent bulk
            user-cache refresh (throttled to avoid spamming ``LoadUsers``).
    """

    instance_key: str
    phone_number: str
    client: Any = None
    message_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    ws_task: Optional[asyncio.Task] = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    session_dir: Path = field(default_factory=lambda: Path("./data/bale_pv_sessions"))
    auth_state: str = "unauthenticated"  # unauthenticated | code_sent | authenticated
    transaction_hash: Optional[str] = None
    session_id: Optional[str] = None  # UUID that scopes the on-disk session file
    user_cache: Dict[int, str] = field(default_factory=dict)  # uid -> display name
    chat_title_cache: Dict[int, str] = field(default_factory=dict)  # peer_id -> title
    self_user_id: Optional[int] = None  # extracted from JWT payload after login
    last_user_cache_refresh: float = 0.0  # unix timestamp of last bulk user refresh


class BalePvConnector:
    """Singleton connector for Bale personal-account (userbot) sessions.

    Each Wootify instance maps to exactly one ``BalePvInstanceRuntime``.
    Public methods are async-safe and identified by ``instance_key``.

    Typical lifecycle::

        await bale_pv.connect(key, config)   # authenticate / resume session
        updates = await bale_pv.get_updates(key)
        await bale_pv.send_text(key, peer_id, text)
        await bale_pv.disconnect(key)
    """

    def __init__(self) -> None:
        self._instances: Dict[str, BalePvInstanceRuntime] = {}
        self._logger = logging.getLogger("app.connectors.bale_pv")

    def _get_runtime(self, instance: str) -> BalePvInstanceRuntime:
        runtime = self._instances.get(instance)
        if not runtime:
            raise RuntimeError(f"Bale PV instance '{instance}' is not configured")
        return runtime

    @staticmethod
    def _normalize_bale_phone(phone: str) -> str:
        """Normalize an Iranian phone number for Bale auth.

        Strips non-digits. If it starts with 0, replaces with 98.
        Examples: 09136421196 → 989136421196, +989136421196 → 989136421196
        """
        digits = re.sub(r"\D", "", str(phone or "").strip())
        if digits.startswith("0") and len(digits) == 11:
            digits = "98" + digits[1:]
        elif digits.startswith("+"):
            digits = digits[1:]
        return digits

    @staticmethod
    def _send_type_for_filename(filename: str, mime_type: str) -> int:
        """Map a filename/mime-type to Bale's SendTypeValue category."""
        from bale_grpc_client.messaging_messages import SendTypeValue

        lower_name = str(filename or "").lower()
        lower_mime = str(mime_type or "").lower()

        if lower_mime.startswith("image/"):
            if lower_mime == "image/webp" or lower_name.endswith(".webp"):
                return SendTypeValue.SEND_TYPE_STICKER
            if lower_name.endswith(".gif"):
                return SendTypeValue.SEND_TYPE_GIF
            return SendTypeValue.SEND_TYPE_PHOTO
        if lower_mime.startswith("video/") or lower_name.endswith(".mp4"):
            return SendTypeValue.SEND_TYPE_VIDEO
        if lower_mime.startswith("audio/") or lower_name.endswith(".ogg"):
            if "voice" in lower_name or lower_mime == "audio/ogg":
                return SendTypeValue.SEND_TYPE_VOICE
            return SendTypeValue.SEND_TYPE_AUDIO
        return SendTypeValue.SEND_TYPE_DOCUMENT

    @staticmethod
    def _media_metadata_for_send(
        *,
        filename: str,
        mime_type: str,
        file_bytes: bytes,
        send_type: int,
    ) -> Tuple[Optional[Any], Optional[Any]]:
        """Return (thumb, ext) metadata for outbound Bale media.

        Uses Pillow for images/videos to produce a FastThumb and dimensions.
        Audio duration is not computed here to avoid heavy dependencies.
        """
        from bale_grpc_client.messaging_messages import FastThumb, ImageExt, AudioExt, SendTypeValue

        lower_mime = str(mime_type or "").lower()

        # Generate thumbnail/dimensions for images (including stickers/webp).
        if send_type in (
            SendTypeValue.SEND_TYPE_PHOTO,
            SendTypeValue.SEND_TYPE_GIF,
            SendTypeValue.SEND_TYPE_STICKER,
        ) or lower_mime.startswith("image/"):
            try:
                from PIL import Image
                from io import BytesIO

                img = Image.open(BytesIO(file_bytes))
                width, height = img.size

                # Create a small thumbnail (max 90px) as JPEG.
                thumb_img = img.copy()
                thumb_img.thumbnail((90, 90))
                if thumb_img.mode in ("RGBA", "P"):
                    thumb_img = thumb_img.convert("RGB")
                thumb_io = BytesIO()
                thumb_img.save(thumb_io, format="JPEG", quality=60)
                thumb_bytes = thumb_io.getvalue()

                return (
                    FastThumb(width=width, height=height, thumb=thumb_bytes),
                    ImageExt(width=width, height=height),
                )
            except Exception as exc:
                logger.debug("bale_pv_thumbnail_failed mime=%s error=%s", mime_type, exc)
                return None, None

        # For video we can at least try to read the first frame with Pillow.
        if send_type == SendTypeValue.SEND_TYPE_VIDEO or lower_mime.startswith("video/"):
            try:
                from PIL import Image
                from io import BytesIO

                img = Image.open(BytesIO(file_bytes))
                width, height = img.size

                thumb_img = img.copy()
                thumb_img.thumbnail((90, 90))
                if thumb_img.mode in ("RGBA", "P"):
                    thumb_img = thumb_img.convert("RGB")
                thumb_io = BytesIO()
                thumb_img.save(thumb_io, format="JPEG", quality=60)
                thumb_bytes = thumb_io.getvalue()

                return (
                    FastThumb(width=width, height=height, thumb=thumb_bytes),
                    ImageExt(width=width, height=height),
                )
            except Exception as exc:
                logger.debug("bale_pv_video_thumb_failed mime=%s error=%s", mime_type, exc)
                return None, None

        # For voice/audio we only set a placeholder AudioExt (duration unknown).
        if send_type in (
            SendTypeValue.SEND_TYPE_VOICE,
            SendTypeValue.SEND_TYPE_AUDIO,
        ) or lower_mime.startswith("audio/"):
            return None, AudioExt(duration=0)

        return None, None

    def _session_path(self, runtime: BalePvInstanceRuntime) -> Path:
        runtime.session_dir.mkdir(parents=True, exist_ok=True)
        if runtime.session_id:
            return runtime.session_dir / f"{runtime.phone_number}_{runtime.session_id}.session"
        # Fallback to old naming for backwards compat during migration
        return runtime.session_dir / f"{runtime.phone_number}.session"

    def _sid_path(self, runtime: BalePvInstanceRuntime) -> Path:
        runtime.session_dir.mkdir(parents=True, exist_ok=True)
        return runtime.session_dir / f"{runtime.instance_key}.sid"

    def _get_or_create_session_id(self, runtime: BalePvInstanceRuntime) -> str:
        """Read existing session ID from .sid file or generate a new one."""
        sid_file = self._sid_path(runtime)
        if sid_file.exists():
            existing = sid_file.read_text().strip()
            if existing:
                return existing
        new_sid = str(uuid.uuid4())
        sid_file.write_text(new_sid, encoding="utf-8")
        self._logger.info(
            "bale_pv session_id_created instance=%s sid=%s",
            runtime.instance_key,
            new_sid,
        )
        return new_sid

    def _migrate_old_session(self, runtime: BalePvInstanceRuntime) -> bool:
        """Copy JWT from old phone-number-only session file to new UUID-based one."""
        old_path = runtime.session_dir / f"{runtime.phone_number}.session"
        if not old_path.exists():
            return False
        new_path = self._session_path(runtime)
        if new_path.exists():
            return False
        try:
            content = old_path.read_text(encoding="utf-8")
            new_path.write_text(content, encoding="utf-8")
            self._logger.info(
                "bale_pv session_migrated instance=%s old=%s new=%s",
                runtime.instance_key,
                old_path.name,
                new_path.name,
            )
            return True
        except Exception as exc:
            self._logger.warning(
                "bale_pv session_migration_failed instance=%s error=%s",
                runtime.instance_key,
                exc,
            )
            return False

    def _cleanup_old_sessions(self, runtime: BalePvInstanceRuntime) -> None:
        """Remove old phone-number-only session files for this PC."""
        try:
            for f in runtime.session_dir.glob("*.session"):
                # Keep UUID-based sessions (contain underscore)
                if "_" not in f.name:
                    f.unlink()
                    self._logger.info(
                        "bale_pv old_session_removed file=%s",
                        f.name,
                    )
        except Exception as exc:
            self._logger.warning("bale_pv old_session_cleanup_failed error=%s", exc)

    @staticmethod
    def _extract_user_id_from_jwt(jwt: str) -> Optional[int]:
        """Extract user_id from JWT payload without verification."""
        try:
            import base64
            parts = jwt.split(".")
            if len(parts) < 2:
                return None
            payload_b64 = parts[1]
            # Pad base64 if needed
            pad = 4 - len(payload_b64) % 4
            if pad != 4:
                payload_b64 += "=" * pad
            payload_json = base64.urlsafe_b64decode(payload_b64).decode("utf-8")
            payload = json.loads(payload_json)
            # Bale JWT: payload.payload.user_id
            inner = payload.get("payload", {})
            uid = inner.get("user_id")
            return int(uid) if uid is not None else None
        except Exception:
            return None

    def _load_session_jwt(self, runtime: BalePvInstanceRuntime) -> Optional[str]:
        """Load JWT string from session file."""
        session_file = self._session_path(runtime)
        if not session_file.exists():
            return None
        try:
            content = session_file.read_text().strip()
            if content.startswith("jwt:") and len(content) > 20:
                return content[4:]
            if content.startswith("{"):
                data = json.loads(content)
                return str(data.get("jwt") or "")
            return None
        except Exception:
            return None

    def _has_valid_session(self, runtime: BalePvInstanceRuntime) -> bool:
        session_file = self._session_path(runtime)
        if not session_file.exists():
            return False
        try:
            content = session_file.read_text().strip()
            # Format 1: plain text "jwt:..."
            if content.startswith("jwt:") and len(content) > 20:
                return True
            # Format 2: JSON with "jwt" field
            if content.startswith("{"):
                data = json.loads(content)
                return bool(data.get("jwt"))
            return False
        except Exception:
            return False

    async def connect(
        self,
        instance: str,
        params: Dict[str, Any],
        proxy: Optional[dict[str, Any]] = None,
    ) -> None:
        """Initialize or refresh connector runtime for a specific instance.

        If a valid session exists, starts the WebSocket listener.
        Otherwise leaves the runtime in 'unauthenticated' state.
        """
        phone_number_raw = str(params.get("bale_pv_phone_number") or "").strip()
        if not phone_number_raw:
            raise RuntimeError(f"Bale PV instance '{instance}' missing bale_pv_phone_number")
        phone_number = self._normalize_bale_phone(phone_number_raw)

        existing = self._instances.get(instance)
        if existing and existing.phone_number == phone_number:
            # Already connected with same phone — preserve auth state and session
            if self._has_valid_session(existing):
                existing.auth_state = "authenticated"
            return

        if existing:
            await self.disconnect(instance)

        session_dir = Path(str(params.get("bale_pv_session_dir") or "./data/bale_pv_sessions"))
        session_dir.mkdir(parents=True, exist_ok=True)

        # NOTE: Bale PV userbot mode requires gRPC-Web/protobuf auth (next-ws.bale.ai).
        # Balethon only supports bot tokens via HTTP Bot API, not phone auth.
        # The full implementation would need a custom gRPC-Web client for Bale.
        # For now, store runtime without an actual client so auth endpoints work.
        runtime = BalePvInstanceRuntime(
            instance_key=instance,
            phone_number=phone_number,
            client=None,
            session_dir=session_dir,
        )
        self._instances[instance] = runtime

        # Ensure each instance has its own UUID-based session
        runtime.session_id = self._get_or_create_session_id(runtime)
        self._migrate_old_session(runtime)
        self._cleanup_old_sessions(runtime)

        if self._has_valid_session(runtime):
            runtime.auth_state = "authenticated"
            # Extract self user ID from JWT so we can skip outgoing message echoes
            jwt = self._load_session_jwt(runtime)
            if jwt:
                runtime.self_user_id = self._extract_user_id_from_jwt(jwt)
            self._logger.info(
                "bale_pv session_exists instance=%s phone=%s sid=%s self_uid=%s",
                instance,
                phone_number,
                runtime.session_id,
                runtime.self_user_id,
            )
            # Attempt to start messaging client (may fail if JWT expired)
            await self._start_messaging_client(runtime)
            # Refresh user cache for contact name lookups
            await self._refresh_user_cache(runtime)
        else:
            self._logger.info(
                "bale_pv awaiting_auth instance=%s phone=%s sid=%s",
                instance,
                phone_number,
                runtime.session_id,
            )

    async def disconnect(self, instance: str) -> None:
        """Stop connector runtime for a specific instance."""
        runtime = self._instances.pop(instance, None)
        if not runtime:
            return
        runtime.stop_event.set()
        if runtime.ws_task and not runtime.ws_task.done():
            runtime.ws_task.cancel()
            try:
                await runtime.ws_task
            except asyncio.CancelledError:
                pass
        if runtime.client is not None:
            try:
                await runtime.client.disconnect()
            except Exception:
                pass
        self._logger.info("bale_pv disconnected instance=%s", instance)

    async def send_text(
        self,
        instance: str,
        chat_id: str,
        text: str,
        quoted: Optional[Dict] = None,
        reply_markup: Any = None,
        access_hash: Optional[int] = None,
    ) -> Dict:
        """Send a text message via the userbot."""
        runtime = self._get_runtime(instance)
        if runtime.auth_state != "authenticated":
            raise RuntimeError(f"Instance {instance} is not authenticated")

        if runtime.client is None:
            self._logger.warning(
                "bale_pv messaging_client_not_connected instance=%s",
                instance,
            )
            raise RuntimeError(
                "Bale PV messaging client is not connected. "
                "Please re-authenticate if the session has expired."
            )

        try:
            peer_id = int(chat_id)
            reply_to = None
            if quoted:
                reply_to_val = quoted.get("message_id") or quoted.get("id")
                if reply_to_val is not None:
                    reply_to = int(reply_to_val)

            response = await runtime.client.send_message(
                peer_id=peer_id,
                text=text,
                reply_to_message_id=reply_to,
                access_hash=access_hash,
            )
            self._logger.info(
                "bale_pv send_text ok instance=%s chat_id=%s",
                instance,
                chat_id,
            )
            return {"ok": True, "result": {"raw_response": response.hex() if response else None}}
        except Exception as exc:
            self._logger.exception(
                "bale_pv send_text error instance=%s chat_id=%s",
                instance,
                chat_id,
            )
            raise RuntimeError(f"send_text failed: {exc}") from exc

    async def resolve_phone_to_user(
        self,
        instance: str,
        phone_number: str,
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve a raw phone number to a Bale user via contacts import.

        Returns the first user dict from the ImportContacts response, or raises
        if the phone cannot be resolved.
        """
        runtime = self._get_runtime(instance)
        if runtime.auth_state != "authenticated":
            raise RuntimeError(f"Instance {instance} is not authenticated")
        if runtime.client is None:
            raise RuntimeError("Bale PV messaging client is not connected")

        normalized = self._normalize_bale_phone(phone_number)
        if not normalized:
            raise ValueError(f"Invalid phone number: {phone_number}")

        parse = _get_dialog_parser()
        self._logger.info(
            "bale_pv import_contacts instance=%s phone=%s",
            instance,
            redact_secret(normalized),
        )
        raw = await runtime.client.import_contacts(
            phones=[{"phone_number": normalized, "name": name or ""}],
            optimizations=[],
        )
        parsed = parse(raw)
        users = parsed.get("users", [])
        if users:
            user = users[0]
        else:
            # Some Bale accounts only appear in the user_peers list even though
            # the phone exists. Fall back to the peer so we can still message them.
            user_peers = parsed.get("user_peers", [])
            self._logger.info(
                "bale_pv import_contacts_no_users instance=%s phone=%s user_peers=%s",
                instance,
                redact_secret(normalized),
                len(user_peers),
            )
            if user_peers:
                peer = user_peers[0]
                peer_id = peer.get("id")
                if peer_id is None:
                    raise RuntimeError(
                        f"Phone number {redact_secret(normalized)} not found on Bale"
                    )
                user = {
                    "id": peer_id,
                    "access_hash": peer.get("access_hash"),
                }
            else:
                raise RuntimeError(
                    f"Phone number {redact_secret(normalized)} not found on Bale"
                )
        self._logger.info(
            "bale_pv phone_resolved instance=%s phone=%s bale_user_id=%s",
            instance,
            redact_secret(normalized),
            user.get("id"),
        )
        return user

    async def send_text_by_phone(
        self,
        instance: str,
        phone_number: str,
        text: str,
        quoted: Optional[Dict] = None,
        name: Optional[str] = None,
    ) -> Dict:
        """Send a text message to a phone number that is not a contact."""
        user = await self.resolve_phone_to_user(instance, phone_number, name=name)
        access_hash_str = user.get("access_hash")
        access_hash = int(access_hash_str) if access_hash_str else None
        return await self.send_text(
            instance=instance,
            chat_id=str(user["id"]),
            text=text,
            quoted=quoted,
            access_hash=access_hash,
        )

    async def send_media(
        self,
        instance: str,
        chat_id: str,
        media_url_or_bytes: Any,
        filename: str,
        caption: Optional[str] = None,
        quoted: Optional[Dict] = None,
        reply_markup: Any = None,
        access_hash: Optional[int] = None,
    ) -> Dict:
        """Send media via the userbot.

        Uploads the file to Bale's Nasim storage and sends it as a
        DocumentMessage. Raises if the file cannot be downloaded or uploaded
        so the caller knows the message was not delivered.
        """
        runtime = self._get_runtime(instance)
        if runtime.auth_state != "authenticated":
            raise RuntimeError(f"Instance {instance} is not authenticated")

        if runtime.client is None:
            raise RuntimeError("Bale PV messaging client is not connected")

        # Resolve media to bytes
        file_bytes: Optional[bytes] = None
        if isinstance(media_url_or_bytes, bytes):
            file_bytes = media_url_or_bytes
        elif isinstance(media_url_or_bytes, str):
            self._logger.info(
                "bale_pv media_download_start instance=%s url=%s",
                instance,
                media_url_or_bytes[:120],
            )
            try:
                import httpx
                async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
                    resp = await client.get(media_url_or_bytes)
                    self._logger.info(
                        "bale_pv media_download_status instance=%s status=%s len=%s",
                        instance,
                        resp.status_code,
                        len(resp.content),
                    )
                    if resp.status_code == 200:
                        file_bytes = resp.content
                    else:
                        raise RuntimeError(
                            f"Media download failed: HTTP {resp.status_code} - {resp.text[:200]}"
                        )
            except Exception as exc:
                self._logger.exception(
                    "bale_pv media_download_error instance=%s url=%s error=%s",
                    instance,
                    media_url_or_bytes[:120],
                    exc,
                )
                if isinstance(exc, RuntimeError):
                    raise
                raise RuntimeError(f"Media download failed: {exc}") from exc

        if not file_bytes:
            raise RuntimeError("No media bytes available to send")

        try:
            uploaded = await self._upload_file_to_nasim(
                instance, chat_id, file_bytes, filename, caption, quoted, access_hash
            )
        except Exception as exc:
            self._logger.exception(
                "bale_pv media_upload_error instance=%s error=%s",
                instance,
                exc,
            )
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"Media upload failed: {exc}") from exc

        if not uploaded:
            raise RuntimeError("Media upload failed: no upload result returned")

        return uploaded

    async def _upload_file_to_nasim(
        self,
        instance: str,
        chat_id: str,
        file_bytes: bytes,
        filename: str,
        caption: Optional[str] = None,
        quoted: Optional[Dict] = None,
        access_hash: Optional[int] = None,
    ) -> Optional[Dict]:
        """Upload file to Bale Nasim storage and send as DocumentMessage."""
        import httpx
        from bale_grpc_client.messaging_messages import (
            GetNasimFileUploadUrlRequest,
            SendMessageRequest,
            DocumentMessage,
            Peer,
            SendTypeValue,
        )
        from bale_grpc_client.protobuf_wire import (
            grpc_web_frame,
            parse_grpc_web_response,
            ProtobufMessage,
            ProtobufParser,
        )

        runtime = self._get_runtime(instance)
        peer_id = int(chat_id)

        # Resolve mime type and Bale send category.
        import mimetypes
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        send_type = self._send_type_for_filename(filename, mime_type)

        # Build upload URL request
        req = GetNasimFileUploadUrlRequest(
            expected_size=len(file_bytes),
            name=filename,
            mime_type=mime_type,
            uid=peer_id,
            send_type=send_type,
            peer_type=Peer.PEER_TYPE_USER,
            access_hash=access_hash or 0,
        )

        session_file = self._session_path(runtime)
        jwt_raw = session_file.read_text().strip()
        if jwt_raw.startswith("{"):
            jwt = json.loads(jwt_raw).get("jwt", "")
        else:
            jwt = jwt_raw[4:] if jwt_raw.startswith("jwt:") else jwt_raw

        upload_timeout = settings.BALE_PV_MEDIA_UPLOAD_TIMEOUT_SECONDS
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=upload_timeout,
        ) as client:
            # Establish cookie session with retries on transient network errors.
            cookie_resp = None
            last_cookie_err = None
            for attempt in range(1, 4):
                try:
                    cookie_resp = await client.post(
                        "https://next-ws.bale.ai/set-cookie/",
                        headers={
                            "Authorization": f"Bearer {jwt}",
                            "Origin": "https://web.bale.ai",
                        },
                    )
                    break
                except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ReadTimeout) as exc:
                    last_cookie_err = exc
                    self._logger.warning(
                        "bale_pv set_cookie_retry instance=%s attempt=%s/%s error=%s",
                        instance,
                        attempt,
                        3,
                        exc,
                    )
                    if attempt < 3:
                        await asyncio.sleep(2 ** attempt)
            if cookie_resp is None:
                raise RuntimeError(
                    f"Bale set-cookie failed after retries: {last_cookie_err}"
                )
            if cookie_resp.status_code != 200:
                raise RuntimeError(
                    f"Bale set-cookie failed: HTTP {cookie_resp.status_code}"
                )

            # Call GetNasimFileUploadUrl with retries on transient network errors.
            resp = None
            last_upload_url_err = None
            for attempt in range(1, 4):
                try:
                    resp = await client.post(
                        "https://next-ws.bale.ai/ai.bale.server.Files/GetNasimFileUploadUrl",
                        content=grpc_web_frame(req.serialize()),
                        headers={
                            "content-type": "application/grpc-web+proto",
                            "x-grpc-web": "1",
                            "mt_app_version": "157595",
                            "app_version": "157595",
                            "browser_type": "1",
                            "mt_browser_type": "1",
                            "browser_version": "148.0.0.0",
                            "mt_browser_version": "148.0.0.0",
                            "os_type": "3",
                            "mt_os_type": "3",
                            "session_id": str(int(time.time() * 1000)),
                            "mt_session_id": str(int(time.time() * 1000)),
                        },
                    )
                    break
                except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ReadTimeout) as exc:
                    last_upload_url_err = exc
                    self._logger.warning(
                        "bale_pv get_upload_url_retry instance=%s attempt=%s/%s error=%s",
                        instance,
                        attempt,
                        3,
                        exc,
                    )
                    if attempt < 3:
                        await asyncio.sleep(2 ** attempt)
            if resp is None:
                raise RuntimeError(
                    f"GetNasimFileUploadUrl failed after retries: {last_upload_url_err}"
                )

            msg, status, grpc_msg = parse_grpc_web_response(resp.content)
            self._logger.info(
                "bale_pv upload_url_raw_hex instance=%s hex=%s",
                instance,
                msg.hex() if msg else "empty",
            )
            if status != 0:
                raise RuntimeError(
                    f"GetNasimFileUploadUrl failed: gRPC status {status} - {grpc_msg}"
                )

            # Parse upload URL response
            fields = ProtobufParser(msg).parse()
            upload_url = ""
            file_id = 0
            file_access_hash = 0
            chunk_size = len(file_bytes)
            self._logger.info("bale_pv upload_url_response fields=%s", fields)
            for key, vals in fields.items():
                if key == 2 and vals:
                    val = vals[0]
                    if isinstance(val, bytes):
                        val = val.decode("utf-8", errors="replace")
                    if isinstance(val, str) and val.startswith("http"):
                        upload_url = val
                elif key == 1 and vals:
                    v = vals[0]
                    if isinstance(v, int):
                        file_id = v
                    elif isinstance(v, bytes) and len(v) == 8:
                        # fixed64 encoding
                        file_id = int.from_bytes(v, "little")
                elif key == 3 and vals:
                    v = vals[0]
                    if isinstance(v, int) and v != 0:
                        # varint-encoded access_hash
                        file_access_hash = v
                    elif isinstance(v, bytes) and len(v) == 8:
                        # fixed64-encoded access_hash
                        file_access_hash = int.from_bytes(v, "little")
                elif key == 4 and vals and isinstance(vals[0], int):
                    chunk_size = vals[0]

            # Fallback: the server has been observed to accept the peer_id as
            # DocumentMessage.access_hash when the upload response doesn't carry
            # an explicit file access_hash (field 3 absent or zero).  Using 0
            # causes the server to reject with InvalidFileLocation.
            if file_access_hash == 0 and peer_id != 0:
                self._logger.info(
                    "bale_pv upload_url_no_file_access_hash instance=%s falling_back_to_peer_id=%s",
                    instance,
                    peer_id,
                )
                file_access_hash = peer_id

            if not upload_url:
                raise RuntimeError(
                    f"GetNasimFileUploadUrl returned no upload URL: {fields}"
                )
            if not file_id:
                raise RuntimeError(
                    f"GetNasimFileUploadUrl returned no file_id: {fields}"
                )

            self._logger.info(
                "bale_pv uploading file instance=%s url=%s size=%s file_id=%s chunk_size=%s",
                instance,
                upload_url[:120],
                len(file_bytes),
                file_id,
                chunk_size,
            )

            # Upload file bytes via PUT (matching Balethon behaviour).
            # The signed URL was requested for a specific Content-Type, so we
            # must include it (and Content-Length) in the PUT.
            try:
                upload_resp = await client.put(
                    upload_url,
                    content=file_bytes,
                    headers={
                        "content-type": mime_type,
                        "content-length": str(len(file_bytes)),
                    },
                )
            except (httpx.ConnectTimeout, httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as exc:
                raise RuntimeError(
                    f"File upload to Nasim timed out (size={len(file_bytes)} bytes, "
                    f"timeout={upload_timeout}s). Consider increasing "
                    f"BALE_PV_MEDIA_UPLOAD_TIMEOUT_SECONDS: {exc}"
                ) from exc
            if upload_resp.status_code not in (200, 201, 204):
                raise RuntimeError(
                    f"File upload to Nasim failed: HTTP {upload_resp.status_code} - {upload_resp.text[:200]}"
                )

            self._logger.info(
                "bale_pv file_uploaded instance=%s file_id=%s size=%s",
                instance,
                file_id,
                len(file_bytes),
            )

            # Build optional media metadata (thumb + dimensions/duration) so the
            # Bale client renders photos/videos/voice correctly instead of as
            # generic documents.
            thumb, ext = self._media_metadata_for_send(
                filename=filename,
                mime_type=mime_type,
                file_bytes=file_bytes,
                send_type=send_type,
            )

            # Send document message via WebSocket
            reply_to = None
            if quoted:
                reply_to_val = quoted.get("message_id") or quoted.get("id")
                if reply_to_val is not None:
                    reply_to = int(reply_to_val)

            self._logger.info(
                "bale_pv sending_document instance=%s peer_id=%s file_id=%s file_access_hash=%s peer_access_hash=%s",
                instance,
                peer_id,
                file_id,
                file_access_hash,
                access_hash,
            )
            await runtime.client.send_document(
                peer_id=peer_id,
                file_id=file_id,
                file_access_hash=file_access_hash,
                file_size=len(file_bytes),
                name=filename,
                mime_type=mime_type,
                caption=caption or None,
                reply_to_message_id=reply_to,
                thumb=thumb,
                ext=ext,
                peer_access_hash=access_hash or 0,
            )
            return {"ok": True, "result": {"file_id": file_id, "name": filename}}

    async def update_message(
        self,
        instance: str,
        chat_id: str,
        message_id: str,
        text: str,
    ) -> Dict[str, Any]:
        """Edit an existing message via the userbot.

        Args:
            instance: Connector instance key.
            chat_id: Target peer ID (as string).
            message_id: Bale message rid (as string).
            text: New message text.

        Returns:
            Dict with ``ok`` status and raw response metadata.
        """
        runtime = self._get_runtime(instance)
        if runtime.auth_state != "authenticated":
            raise RuntimeError(f"Instance {instance} is not authenticated")

        try:
            peer_id = int(chat_id)
            rid = int(message_id)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"Invalid chat_id or message_id: {exc}") from exc

        try:
            response = await runtime.client.update_message(
                peer_id=peer_id,
                message_id=rid,
                text=text,
            )
            self._logger.info(
                "bale_pv update_message ok instance=%s chat_id=%s message_id=%s",
                instance,
                chat_id,
                message_id,
            )
            return {
                "ok": True,
                "result": {"raw_response": response.hex() if response else None},
            }
        except Exception as exc:
            self._logger.exception(
                "bale_pv update_message error instance=%s chat_id=%s message_id=%s",
                instance,
                chat_id,
                message_id,
            )
            raise RuntimeError(f"update_message failed: {exc}") from exc

    async def delete_message(
        self,
        instance: str,
        chat_id: str,
        message_id: str,
    ) -> Dict[str, Any]:
        """Delete a message via the userbot.

        Args:
            instance: Connector instance key.
            chat_id: Target peer ID (as string).
            message_id: Bale message rid (as string).

        Returns:
            Dict with ``ok`` status and raw response metadata.
        """
        runtime = self._get_runtime(instance)
        if runtime.auth_state != "authenticated":
            raise RuntimeError(f"Instance {instance} is not authenticated")

        try:
            peer_id = int(chat_id)
            rid = int(message_id)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"Invalid chat_id or message_id: {exc}") from exc

        try:
            response = await runtime.client.delete_message(
                peer_id=peer_id,
                message_ids=[rid],
                just_mine=False,
            )
            self._logger.info(
                "bale_pv delete_message ok instance=%s chat_id=%s message_id=%s",
                instance,
                chat_id,
                message_id,
            )
            return {
                "ok": True,
                "result": {"raw_response": response.hex() if response else None},
            }
        except Exception as exc:
            self._logger.exception(
                "bale_pv delete_message error instance=%s chat_id=%s message_id=%s",
                instance,
                chat_id,
                message_id,
            )
            raise RuntimeError(f"delete_message failed: {exc}") from exc

    async def get_updates(
        self, instance: str, offset: Optional[int] = None, timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """Fetch inbound platform updates from the WebSocket message queue.

        Instead of HTTP long-polling, this drains the internal asyncio.Queue
        that the WebSocket listener populates, then parses raw protobuf
        updates into Bot-API-style update dictionaries.
        """
        from bale_grpc_client.update_parser import parse_ws_update

        runtime = self._get_runtime(instance)
        self._logger.debug(
            "bale_pv get_updates_enter instance=%s auth_state=%s offset=%s timeout=%s",
            instance,
            runtime.auth_state,
            offset,
            timeout,
        )

        if runtime.auth_state != "authenticated":
            self._logger.warning(
                "bale_pv get_updates_not_authenticated instance=%s auth_state=%s",
                instance,
                runtime.auth_state,
            )
            return {
                "ok": False,
                "description": "not_authenticated",
                "result": [],
            }

        if not runtime.ws_task or runtime.ws_task.done():
            await self._start_messaging_client(runtime)

        # Ensure user cache is populated for contact name lookups
        cache_stale = (time.time() - runtime.last_user_cache_refresh) > 300  # 5 minutes
        if not runtime.user_cache or cache_stale:
            await self._refresh_user_cache(runtime)

        updates: List[Dict[str, Any]] = []
        max_items = 50
        wait_seconds = min(timeout or 1, 5)

        raw_updates: List[Any] = []
        try:
            # Wait for at least one raw protobuf update, then drain the queue
            raw = await asyncio.wait_for(runtime.message_queue.get(), timeout=wait_seconds)
            if raw:
                raw_updates.append(raw)
            # Drain remaining without blocking
            for _ in range(max_items - 1):
                try:
                    raw = runtime.message_queue.get_nowait()
                    if raw:
                        raw_updates.append(raw)
                except asyncio.QueueEmpty:
                    break
        except asyncio.TimeoutError:
            pass

        # First-pass parse to discover unknown senders and missing group titles.
        for raw in raw_updates:
            parsed = self._parse_raw_update(raw, runtime.user_cache, runtime.self_user_id, runtime.chat_title_cache)
            if parsed:
                updates.append(parsed)

        # Resolve missing group/channel titles on demand so Chatwoot contacts
        # show real names instead of "Group {id}" / "Channel {id}".
        missing_group_peers: Dict[int, int] = {}
        for up in updates:
            message = up.get("message") or {}
            chat = message.get("chat") or {}
            chat_type = str(chat.get("type") or "").lower()
            title = str(chat.get("title") or "").strip()
            chat_id = chat.get("id")
            if chat_type in ("group", "channel") and chat_id is not None:
                try:
                    peer_id = int(chat_id)
                except (ValueError, TypeError):
                    continue
                if title.startswith("Group ") or title.startswith("Channel "):
                    peer_type = 3 if chat_type == "channel" else 2
                    missing_group_peers[peer_id] = peer_type

        if missing_group_peers:
            self._logger.info(
                "bale_pv resolving_missing_group_titles instance=%s peers=%s",
                instance,
                sorted(missing_group_peers.keys()),
            )
            for peer_id, peer_type in missing_group_peers.items():
                await self._resolve_group_title(instance, peer_id, peer_type)

        # Resolve names for senders not in the contact cache.
        user_info_map = await self._resolve_unknown_sender_info(runtime, updates)
        if user_info_map or missing_group_peers:
            # Re-parse with resolved user info and updated title cache so
            # names and group titles are accurate.
            updates = []
            for raw in raw_updates:
                parsed = self._parse_raw_update(
                    raw,
                    runtime.user_cache,
                    runtime.self_user_id,
                    runtime.chat_title_cache,
                    user_info_map,
                )
                if parsed:
                    updates.append(parsed)

        self._logger.debug(
            "bale_pv get_updates_exit instance=%s update_count=%s",
            instance,
            len(updates),
        )
        return {
            "ok": True,
            "result": updates,
        }

    async def _resolve_unknown_sender_info(
        self,
        runtime: BalePvInstanceRuntime,
        updates: List[Dict[str, Any]],
    ) -> Dict[int, Dict[str, Any]]:
        """Fetch Bale user details for senders not present in the contact cache.

        Returns a mapping of uid -> user info dict with keys like name, nick,
        access_hash. Also back-fills the runtime user_cache with display names.
        """
        from bale_grpc_client.dialog_parser import parse_load_users_response

        result: Dict[int, Dict[str, Any]] = {}
        unknown_uids: set[int] = set()
        uid_to_access_hash: Dict[int, int] = {}
        for update in updates:
            # Updates from _parse_raw_update are wrapped as
            # {"update_id": ..., "message": {...}}.
            message = update.get("message") or update
            sender = message.get("from") or {}
            uid = sender.get("id")
            if not isinstance(uid, int):
                continue
            if uid not in runtime.user_cache:
                unknown_uids.add(uid)
                access_hash = message.get("_sender_access_hash") or message.get("sender_access_hash")
                if isinstance(access_hash, int):
                    uid_to_access_hash[uid] = access_hash

        if not unknown_uids or not runtime.client:
            return result

        try:
            user_peers = [
                {"uid": uid, "access_hash": uid_to_access_hash.get(uid, 0)}
                for uid in unknown_uids
            ]
            raw_response = await runtime.client.load_users(user_peers)
            parsed = parse_load_users_response(raw_response)
            for user in parsed.get("users", []):
                uid = user.get("id")
                if not isinstance(uid, int):
                    continue
                result[uid] = user
                # Cache display name for future lookups.
                name = user.get("name") or user.get("nick")
                if name:
                    runtime.user_cache[uid] = str(name).strip()
        except Exception as exc:
            self._logger.warning(
                "bale_pv resolve_unknown_sender_info_failed instance=%s uids=%s error=%s",
                runtime.instance_key,
                sorted(unknown_uids),
                exc,
            )

        return result

    @staticmethod
    def _parse_raw_update(
        raw: Any,
        user_cache: Optional[Dict[int, str]] = None,
        self_user_id: Optional[int] = None,
        chat_title_cache: Optional[Dict[int, str]] = None,
        user_info_map: Optional[Dict[int, Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Parse a raw protobuf update into a Bot-API-style update dict.

        Args:
            raw: Raw protobuf bytes or already-parsed dict.
            user_cache: uid -> display name cache (from contacts).
            self_user_id: UID of the authenticated account.
            chat_title_cache: Optional cache of group/channel titles.
            user_info_map: Optional uid -> {name, nick, access_hash, ...} fetched
                via LoadUsers for senders not present in the contact cache.
        """
        from bale_grpc_client.update_parser import parse_ws_update

        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, bytes):
            return None

        # Try to parse as WebSocket update frame
        parsed = parse_ws_update(raw)
        if not parsed:
            return None

        sender_uid = parsed.get("sender_uid")
        # Must have integer sender_uid to be a real message
        if not isinstance(sender_uid, int):
            return None

        # Preserve the sender access_hash when the update includes it. This is
        # required to resolve non-contact group senders via LoadUsers.
        sender_access_hash = parsed.get("sender_access_hash")

        event_type = parsed.get("type", "message")

        # For channel/broadcast messages the authoritative chat peer is channel_peer.
        channel_peer = parsed.get("channel_peer")
        if event_type == "channel_message" and isinstance(channel_peer, dict) and channel_peer.get("id"):
            peer = channel_peer
        else:
            peer = parsed.get("peer") or {}
        peer_id = peer.get("id") or sender_uid
        peer_type = peer.get("type", 1)

        # For group/channel messages the real sender user id is often carried in
        # the field-9 peer (int64) while the legacy senderUid field (2) can be a
        # truncated/local id. Prefer the peer uid when we have it so names and
        # contact identifiers are consistent.
        sender_peer_uid = parsed.get("sender_peer_uid")
        if peer_type in (2, 3) and isinstance(sender_peer_uid, int):
            sender_uid = sender_peer_uid

        # Detect whether this is an outgoing echo (a message sent from another
        # Bale client that the server mirrors back to keep all sessions in sync).
        # For group messages, sender_uid was already overridden to sender_peer_uid
        # above, so a plain equality check covers both private and group cases.
        #
        # IMPORTANT: Do NOT use "sender_peer_uid == self_user_id" here.  For
        # incoming private messages, Bale populates field 9 with the RECIPIENT's
        # peer info (i.e. the authenticated account), so sender_peer_uid equals
        # self_user_id even for genuinely incoming messages.  Including that
        # condition in the OR would wrongly flag every incoming 1-on-1 message as
        # outgoing, causing the contact to be named after the authenticated
        # account instead of the actual sender.
        is_outgoing = self_user_id is not None and sender_uid == self_user_id

        # Determine chat_id based on peer type
        if is_outgoing:
            # Outgoing echo: the conversation is the peer we sent to
            chat_id = str(peer_id)
        elif peer_type == 1:
            # Incoming 1:1 message
            chat_id = str(sender_uid)
        else:
            # Incoming group/channel message: conversation is the group/channel
            chat_id = str(peer_id)

        # Map peer type to Bot-API chat type so Chatwoot can label groups/channels.
        chat_type = {1: "private", 2: "group", 3: "channel"}.get(peer_type, "private")
        # Wrapper-level channel messages sometimes report peer_type 2 in the outer
        # peer but carry a channel_peer; force channel classification in that case.
        if event_type == "channel_message" and chat_type == "group":
            chat_type = "channel"

        # Look up sender info. Prefer freshly fetched LoadUsers data, then contact cache.
        info = (user_info_map or {}).get(sender_uid, {})
        cached_name = ""
        if user_cache and isinstance(sender_uid, int):
            cached_name = user_cache.get(sender_uid, "")
        display_name = info.get("name") or cached_name
        nick = info.get("nick")
        username = nick or display_name or f"user_{sender_uid}"
        # Prefer the resolved display name. If none is available, fall back to
        # the username/nick so group messages don't show the raw numeric id.
        sender_label = display_name
        if not sender_label:
            if username and not username.lower().startswith("user_"):
                sender_label = username
            else:
                sender_label = f"User {sender_uid}"

        # Build a chat title so the Chatwoot contact name can be labeled correctly.
        # For groups/channels this is the group/channel title; for PV it is the peer's name.
        _chat_title_cache = chat_title_cache or {}
        if peer_type == 1:
            title_peer_id = int(peer_id) if is_outgoing else sender_uid
            chat_title = _chat_title_cache.get(int(title_peer_id), "")
            if not chat_title:
                chat_title = display_name or f"User {title_peer_id}"
        elif peer_type in (2, 3):
            cached_title = _chat_title_cache.get(int(chat_id), "")
            if cached_title:
                # If the cached dialog title is labeled as a channel, treat it as one.
                if cached_title.startswith("(channel)"):
                    chat_type = "channel"
                # Dialog cache stores titles like "(group) Team" or "(channel) News".
                # Strip the type label so Chatwoot shows the real name.
                chat_title = cached_title
                for label in ("(group)", "(channel)"):
                    if chat_title.startswith(label):
                        stripped = chat_title[len(label):].strip()
                        if stripped:
                            chat_title = stripped
                        break
            elif event_type == "channel_message" or peer_type == 3:
                chat_title = f"Channel {chat_id}"
            else:
                chat_title = f"Group {chat_id}"
        else:
            chat_title = f"Chat {chat_id}"

        text = parsed.get("text") or ""

        # For group/channel messages, prefix with sender name so members are distinguishable.
        # If the sender is not in the cache yet, label them generically instead of a raw ID.
        if peer_type in (2, 3) and not is_outgoing and text:
            text = f"{sender_label}: {text}"

        rid = parsed.get("rid") or parsed.get("message_id")
        date = parsed.get("date")
        media = parsed.get("media")

        message: Dict[str, Any] = {
            "message_id": str(rid) if rid else None,
            "date": int(date or 0),
            "chat": {"id": chat_id, "type": chat_type, "title": chat_title},
            "from": {
                "id": sender_uid,
                "first_name": sender_label,
                "username": username,
            },
            "text": text,
        }
        if isinstance(sender_access_hash, int):
            message["_sender_access_hash"] = sender_access_hash
        if is_outgoing:
            message["_outgoing"] = True

        # Pass through reply-to reference so reply threading works
        reply_to_msg_id = parsed.get("reply_to_msg_id")
        if reply_to_msg_id is not None:
            message["reply_to_message"] = {"message_id": str(reply_to_msg_id)}

        # Attach media metadata for downstream processing.
        # Detect the media category from the declared MIME type and set the
        # appropriate Bot-API field so _extract_file can route it correctly.
        if media:
            composite = {
                "file_id": media.get("file_id"),
                "access_hash": media.get("access_hash"),
                "peer_id": peer_id,
                "file_name": media.get("file_name", ""),
                "file_storage_version": media.get("file_storage_version", 0),
            }
            media["file_id"] = json.dumps(composite, separators=(",", ":"))

            mime = str(media.get("mime_type") or "").strip().lower()
            file_name = str(media.get("file_name") or "").strip().lower()
            width = media.get("width")
            height = media.get("height")

            # Build a base dict that _extract_file will read
            media_entry = {
                "file_id": media["file_id"],
                "file_name": media.get("file_name", ""),
                "mime_type": media.get("mime_type", ""),
            }
            if width is not None:
                media_entry["width"] = width
            if height is not None:
                media_entry["height"] = height

            if mime.startswith("image/"):
                # Bale sends stickers with mime_type="image/jpeg" but filename
                # "sticker<id>.png" — the actual bytes are WEBP. Detect by
                # mime, extension, OR by the "sticker" filename prefix.
                _is_sticker = (
                    mime == "image/webp"
                    or file_name.endswith(".webp")
                    or file_name.startswith("sticker")
                )
                if _is_sticker:
                    # Treat WEBP as stickers (Bale/Telegram convention).
                    # We intentionally omit the thumbnail file_id because Bale
                    # does not expose a separate thumbnail file; including the
                    # same composite JSON under thumbnail.file_id confuses the
                    # downstream extractor. Width/height are preserved so UI
                    # can render the sticker at the right aspect ratio.
                    sticker_thumb: Dict[str, Any] = {}
                    if width is not None:
                        sticker_thumb["width"] = width
                    if height is not None:
                        sticker_thumb["height"] = height
                    message["sticker"] = {
                        "file_id": media["file_id"],
                        # Always tag stickers as image/webp regardless of what
                        # Bale declares (e.g. "image/jpeg" for sticker*.png files).
                        "mime_type": "image/webp",
                        "thumbnail": sticker_thumb if sticker_thumb else None,
                    }
                else:
                    # Photos: Bot-API expects a list, last element is used
                    message["photo"] = [media_entry]
            elif mime.startswith("video/"):
                message["video"] = media_entry
            elif mime.startswith("audio/"):
                if mime == "audio/ogg" or file_name.endswith(".ogg"):
                    # Voice messages are typically OGG in Bale
                    message["voice"] = media_entry
                else:
                    message["audio"] = media_entry
            else:
                message["document"] = media_entry

            message["mime_type"] = media.get("mime_type", "")
            message["file_name"] = media.get("file_name", "")
            if width is not None:
                message["width"] = width
            if height is not None:
                message["height"] = height

        return {
            "update_id": int(rid or 0),
            "message": message,
        }

    @staticmethod
    def _extract_url_from_nasim_response(msg: bytes) -> Optional[str]:
        """Try multiple protobuf field layouts to extract the download URL.

        Bale has changed the ``GetNasimFileUrl`` response schema in the past.
        We try the most common layouts and fall back gracefully.
        """
        from bale_grpc_client.protobuf_wire import ProtobufParser

        fields = ProtobufParser(msg).parse()

        # Layout A: field 1 = nested message, field 2 of that nested msg = url string
        file_url_bytes = fields.get(1, [None])[0]
        if isinstance(file_url_bytes, bytes) and len(file_url_bytes) > 4:
            try:
                url_fields = ProtobufParser(file_url_bytes).parse()
                url_val = url_fields.get(2, [b""])[0]
                if isinstance(url_val, bytes) and url_val:
                    return url_val.decode("utf-8", errors="replace")
            except Exception:
                pass

        # Layout B: field 1 = direct string URL
        if isinstance(file_url_bytes, bytes) and file_url_bytes.startswith(b"http"):
            return file_url_bytes.decode("utf-8", errors="replace")

        # Layout C: field 2 = direct string URL
        url_val = fields.get(2, [b""])[0]
        if isinstance(url_val, bytes) and url_val.startswith(b"http"):
            return url_val.decode("utf-8", errors="replace")

        # Layout D: field 1 = nested message, field 1 of that nested msg = url string
        if isinstance(file_url_bytes, bytes) and len(file_url_bytes) > 4:
            try:
                url_fields = ProtobufParser(file_url_bytes).parse()
                url_val = url_fields.get(1, [b""])[0]
                if isinstance(url_val, bytes) and url_val:
                    return url_val.decode("utf-8", errors="replace")
            except Exception:
                pass

        return None

    @staticmethod
    def _extract_url_from_nasim_urls_response(
        msg: bytes, target_file_id: int
    ) -> Optional[str]:
        """Extract the download URL for a specific file_id from GetNasimFileUrls response.

        Response layout observed:
          1: repeated fileUrl { 1: fileId, 2: url, 3: duplicate, 4: chunkSize, 5: blockSize }
        """
        from bale_grpc_client.protobuf_wire import ProtobufParser

        try:
            fields = ProtobufParser(msg).parse()
            for file_url_bytes in fields.get(1, []):
                if not isinstance(file_url_bytes, bytes):
                    continue
                file_url_fields = ProtobufParser(file_url_bytes).parse()
                file_id = file_url_fields.get(1, [None])[0]
                url_val = file_url_fields.get(2, [None])[0]
                if (
                    file_id == target_file_id
                    and isinstance(url_val, bytes)
                    and url_val.startswith(b"http")
                ):
                    return url_val.decode("utf-8", errors="replace")
        except Exception:
            pass
        return None

    @staticmethod
    def _is_valid_image_bytes(content: bytes) -> bool:
        """Quick sanity check that bytes look like a known image format."""
        if not content:
            return False
        return (
            content.startswith(b"\x89PNG\r\n\x1a\n")
            or content.startswith(b"\xff\xd8\xff")
            or content.startswith((b"GIF87a", b"GIF89a"))
            or (len(content) > 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP")
        )

    async def download_file_by_id(
        self, instance: str, file_id: str
    ) -> Tuple[bytes, Optional[str], Optional[str]]:
        """Download a platform file payload by its provider-specific file ID.

        For Bale PV, ``file_id`` is a JSON string containing
        ``{"file_id": int, "access_hash": int, "peer_id": int}``
        so that the ``GetNasimFileUrl`` gRPC call can be constructed.
        """
        runtime = self._get_runtime(instance)
        if runtime.auth_state != "authenticated":
            self._logger.warning("bale_pv download_file_by_id not_authenticated instance=%s", instance)
            return b"", None, None

        # Parse composite file_id
        try:
            file_info = json.loads(file_id)
            fid = int(file_info["file_id"])
            # access_hash may be None (absent in protobuf for public sticker packs)
            ahash = int(file_info.get("access_hash") or 0)
            filename = file_info.get("file_name", "")
            # fileStorageVersion is required by Bale's Nasim service; all live
            # captures show version=1. Defaulting to 0 causes sticker downloads
            # to fail silently.
            file_storage_version = int(file_info.get("file_storage_version") or 0)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            self._logger.warning(
                "bale_pv download_file_by_id invalid file_id format instance=%s file_id=%s error=%s",
                instance,
                file_id,
                exc,
            )
            return b"", None, None

        try:
            import httpx
            from bale_grpc_client.messaging_messages import (
                GetNasimFileUrlRequest,
                GetNasimFileUrlsRequest,
            )
            from bale_grpc_client.protobuf_wire import (
                grpc_web_frame,
                parse_grpc_web_response,
                ProtobufParser,
            )

            session_file = self._session_path(runtime)
            jwt_raw = session_file.read_text().strip()
            if jwt_raw.startswith("{"):
                jwt = json.loads(jwt_raw).get("jwt", "")
            else:
                jwt = jwt_raw[4:] if jwt_raw.startswith("jwt:") else jwt_raw

            peer_id = file_info.get("peer_id")

            async with httpx.AsyncClient(follow_redirects=True) as client:
                # Establish cookie session
                cookie_resp = await client.post(
                    "https://next-ws.bale.ai/set-cookie/",
                    headers={
                        "Authorization": f"Bearer {jwt}",
                        "Origin": "https://web.bale.ai",
                    },
                )
                if cookie_resp.status_code != 200:
                    self._logger.warning(
                        "bale_pv set_cookie_failed instance=%s status=%s",
                        instance,
                        cookie_resp.status_code,
                    )
                    return b"", None, None

                download_url: Optional[str] = None

                # Try GetNasimFileUrl first (single file URL).
                req = GetNasimFileUrlRequest(file_id=fid, access_hash=ahash, file_storage_version=file_storage_version)
                resp = await client.post(
                    "https://next-ws.bale.ai/ai.bale.server.Files/GetNasimFileUrl",
                    content=grpc_web_frame(req.serialize()),
                    headers={
                        "content-type": "application/grpc-web+proto",
                        "x-grpc-web": "1",
                        "mt_app_version": "157595",
                        "app_version": "157595",
                        "browser_type": "1",
                        "mt_browser_type": "1",
                        "browser_version": "148.0.0.0",
                        "mt_browser_version": "148.0.0.0",
                        "os_type": "3",
                        "mt_os_type": "3",
                        "session_id": str(int(time.time() * 1000)),
                        "mt_session_id": str(int(time.time() * 1000)),
                    },
                )

                msg, status, grpc_msg = parse_grpc_web_response(resp.content)
                if status != 0:
                    self._logger.warning(
                        "bale_pv GetNasimFileUrl grpc_error instance=%s status=%s msg=%s",
                        instance,
                        status,
                        grpc_msg,
                    )
                else:
                    download_url = self._extract_url_from_nasim_response(msg)
                    if not download_url:
                        self._logger.warning(
                            "bale_pv GetNasimFileUrl no_url instance=%s msg_len=%s fields=%s",
                            instance,
                            len(msg),
                            msg[:32].hex() if msg else "empty",
                        )

                # Fallback to GetNasimFileUrls (plural) when the single-file call
                # fails or returns no URL. Some forwarded/sticker files need peer
                # context to resolve.
                if not download_url and peer_id is not None:
                    self._logger.info(
                        "bale_pv trying_GetNasimFileUrls instance=%s file_id=%s peer_id=%s",
                        instance,
                        fid,
                        peer_id,
                    )
                    urls_req = GetNasimFileUrlsRequest(
                        peer_id=int(peer_id),
                        files=[{"file_id": fid, "access_hash": ahash, "file_storage_version": file_storage_version}],
                    )
                    urls_resp = await client.post(
                        "https://next-ws.bale.ai/ai.bale.server.Files/GetNasimFileUrls",
                        content=grpc_web_frame(urls_req.serialize()),
                        headers={
                            "content-type": "application/grpc-web+proto",
                            "x-grpc-web": "1",
                            "mt_app_version": "157595",
                            "app_version": "157595",
                            "browser_type": "1",
                            "mt_browser_type": "1",
                            "browser_version": "148.0.0.0",
                            "mt_browser_version": "148.0.0.0",
                            "os_type": "3",
                            "mt_os_type": "3",
                            "session_id": str(int(time.time() * 1000)),
                            "mt_session_id": str(int(time.time() * 1000)),
                        },
                    )
                    urls_msg, urls_status, urls_grpc_msg = parse_grpc_web_response(
                        urls_resp.content
                    )
                    if urls_status != 0:
                        self._logger.warning(
                            "bale_pv GetNasimFileUrls grpc_error instance=%s status=%s msg=%s",
                            instance,
                            urls_status,
                            urls_grpc_msg,
                        )
                    else:
                        download_url = self._extract_url_from_nasim_urls_response(
                            urls_msg, fid
                        )
                        if not download_url:
                            self._logger.warning(
                                "bale_pv GetNasimFileUrls no_url instance=%s msg_len=%s",
                                instance,
                                len(urls_msg),
                            )

                if not download_url:
                    return b"", None, None

                self._logger.info(
                    "bale_pv downloading file instance=%s url=%s",
                    instance,
                    download_url[:120],
                )

                # Download the actual file
                file_resp = await client.get(download_url)
                if file_resp.status_code != 200:
                    self._logger.warning(
                        "bale_pv file_download_failed instance=%s status=%s url=%s",
                        instance,
                        file_resp.status_code,
                        download_url[:80],
                    )
                    return b"", None, None

                content = file_resp.content
                content_type = file_resp.headers.get("content-type")
                self._logger.info(
                    "bale_pv file_downloaded instance=%s size=%s ctype=%s",
                    instance,
                    len(content),
                    content_type,
                )
                return content, content_type, filename or None

        except Exception as exc:
            self._logger.exception(
                "bale_pv download_file_by_id error instance=%s file_id=%s",
                instance,
                file_id,
            )
            return b"", None, None

    async def close(self) -> None:
        """Release connector resources for all tracked instances."""
        for key in list(self._instances.keys()):
            await self.disconnect(key)
        self._instances.clear()

    # ------------------------------------------------------------------
    # Auth helpers (used by API controller)
    # ------------------------------------------------------------------

    async def send_auth_code(self, instance: str) -> Dict[str, Any]:
        """Request SMS auth code for the instance's phone number."""
        runtime = self._get_runtime(instance)
        BaleAuthClient, BaleAuthError = _get_auth_client()

        client = BaleAuthClient()
        try:
            result = await client.start_phone_auth(
                phone_number=runtime.phone_number,
                device_title=f"Wootify {instance}",
                send_code_type=0,
            )
            runtime.transaction_hash = result.get("transaction_hash")
            runtime.auth_state = "code_sent"
            self._logger.info(
                "bale_pv send_auth_code ok instance=%s transaction_hash=%s",
                instance,
                runtime.transaction_hash,
            )
            return {
                "ok": True,
                "transaction_hash": runtime.transaction_hash,
                "is_registered": result.get("is_registered"),
                "activation_type": result.get("activation_type"),
            }
        except BaleAuthError as exc:
            self._logger.warning(
                "bale_pv send_auth_code failed instance=%s error=%s",
                instance,
                exc.message,
            )
            return {"ok": False, "description": exc.message}
        except Exception as exc:
            self._logger.exception(
                "bale_pv send_auth_code error instance=%s",
                instance,
            )
            return {"ok": False, "description": str(exc)}
        finally:
            await client.close()

    async def validate_auth_code(self, instance: str, code: str) -> Dict[str, Any]:
        """Validate SMS code and complete authentication."""
        runtime = self._get_runtime(instance)
        BaleAuthClient, BaleAuthError = _get_auth_client()

        if not runtime.transaction_hash:
            return {"ok": False, "description": "no_pending_auth"}

        client = BaleAuthClient()
        try:
            result = await client.validate_code(
                transaction_hash=runtime.transaction_hash,
                code=code,
                is_jwt=True,
            )
            jwt = result.get("jwt")
            if jwt:
                session_file = self._session_path(runtime)
                session_file.write_text(f"jwt:{jwt}", encoding="utf-8")
                runtime.auth_state = "authenticated"
                self._logger.info(
                    "bale_pv validate_auth_code ok instance=%s",
                    instance,
                )
                return {"ok": True, "jwt_saved": True}
            else:
                runtime.auth_state = "unauthenticated"
                return {"ok": False, "description": "no_jwt_in_response"}
        except BaleAuthError as exc:
            self._logger.warning(
                "bale_pv validate_auth_code failed instance=%s error=%s",
                instance,
                exc.message,
            )
            return {"ok": False, "description": exc.message}
        except Exception as exc:
            self._logger.exception(
                "bale_pv validate_auth_code error instance=%s",
                instance,
            )
            return {"ok": False, "description": str(exc)}
        finally:
            await client.close()

    def get_auth_state(self, instance: str) -> Dict[str, Any]:
        """Get current authentication state for an instance."""
        runtime = self._instances.get(instance)
        if not runtime:
            return {"ok": False, "description": "instance_not_loaded"}
        has_session = self._has_valid_session(runtime)
        return {
            "ok": True,
            "state": runtime.auth_state,
            "phone_number": runtime.phone_number,
            "has_session_file": has_session,
        }

    def get_self_user_id(self, instance: str) -> Optional[int]:
        """Return the authenticated user's own Bale ID (from JWT), or None."""
        runtime = self._instances.get(instance)
        if not runtime:
            return None
        return runtime.self_user_id

    def get_user_name(self, instance: str, user_id: int) -> Optional[str]:
        """Return a cached display name for a Bale user id, if known."""
        runtime = self._instances.get(instance)
        if not runtime:
            return None
        return runtime.user_cache.get(user_id)

    async def get_user_avatar_bytes(
        self,
        instance: str,
        user_id: int,
    ) -> Tuple[Optional[bytes], Optional[str]]:
        """Fetch a user's profile avatar bytes and content type, if available.

        Loads the user profile via ``LoadUsers``, parses the ``Avatar`` field,
        then downloads the photo through the Nasim file URL endpoint.
        Returns ``(bytes, content_type)`` or ``(None, None)`` when no avatar
        is available or the download fails.
        """
        from bale_grpc_client.dialog_parser import parse_load_users_response

        runtime = self._get_runtime(instance)
        if runtime.auth_state != "authenticated" or runtime.client is None:
            return None, None

        try:
            raw = await runtime.client.load_users([{"uid": int(user_id)}])
        except Exception as exc:
            self._logger.warning(
                "bale_pv get_user_avatar load_users_failed instance=%s user_id=%s error=%s",
                instance,
                user_id,
                exc,
            )
            return None, None

        try:
            parsed = parse_load_users_response(raw)
        except Exception as exc:
            self._logger.warning(
                "bale_pv get_user_avatar parse_failed instance=%s user_id=%s error=%s",
                instance,
                user_id,
                exc,
            )
            return None, None

        user = None
        for u in parsed.get("users", []):
            if u.get("id") == user_id:
                user = u
                break

        if not user:
            return None, None

        avatar = user.get("avatar")
        if not avatar:
            self._logger.debug(
                "bale_pv get_user_avatar no_avatar instance=%s user_id=%s",
                instance,
                user_id,
            )
            return None, None

        photo_id = avatar.get("photo_id")
        access_hash = avatar.get("access_hash")
        if photo_id is None:
            self._logger.debug(
                "bale_pv get_user_avatar no_photo_id instance=%s user_id=%s avatar=%s",
                instance,
                user_id,
                avatar,
            )
            return None, None

        file_id_payload = json.dumps({
            "file_id": int(photo_id),
            "access_hash": int(access_hash) if access_hash is not None else 0,
            "peer_id": int(user_id),
            "file_storage_version": 1,
        })
        try:
            content, content_type, _ = await self.download_file_by_id(
                instance, file_id_payload
            )
        except Exception as exc:
            self._logger.warning(
                "bale_pv get_user_avatar download_failed instance=%s user_id=%s error=%s",
                instance,
                user_id,
                exc,
            )
            return None, None

        if content:
            self._logger.info(
                "bale_pv get_user_avatar ok instance=%s user_id=%s size=%s ctype=%s",
                instance,
                user_id,
                len(content),
                content_type,
            )
        return content, content_type

    async def _resolve_group_title(
        self,
        instance: str,
        peer_id: int,
        peer_type: int,
    ) -> Optional[str]:
        """Fetch a group/channel title on demand when it is not cached.

        Calls ``LoadDialogs`` and searches the response for the requested
        peer. If found, the title is stored in ``runtime.chat_title_cache``
        and the raw title (without the ``(group)`` / ``(channel)`` label) is
        returned. Returns ``None`` when the dialog cannot be fetched or the
        peer is not present.
        """
        from bale_grpc_client.dialog_parser import parse_load_dialogs_response
        from bale_grpc_client.messaging_messages import Peer

        runtime = self._get_runtime(instance)
        if runtime.auth_state != "authenticated" or runtime.client is None:
            return None

        try:
            raw = await runtime.client.load_dialogs(limit=200)
        except Exception as exc:
            self._logger.warning(
                "bale_pv resolve_group_title load_dialogs_failed instance=%s peer_id=%s error=%s",
                instance,
                peer_id,
                exc,
            )
            return None

        try:
            parsed = parse_load_dialogs_response(raw)
        except Exception as exc:
            self._logger.warning(
                "bale_pv resolve_group_title parse_failed instance=%s peer_id=%s error=%s",
                instance,
                peer_id,
                exc,
            )
            return None

        groups = parsed.get("groups", [])
        for g in groups:
            gid = g.get("id")
            if gid is not None and int(gid) == peer_id:
                title = g.get("title") or ""
                if title:
                    label = "channel" if peer_type == Peer.PEER_TYPE_CHANNEL else "group"
                    runtime.chat_title_cache[peer_id] = f"({label}) {title}"
                    self._logger.info(
                        "bale_pv resolve_group_title ok instance=%s peer_id=%s title=%s",
                        instance,
                        peer_id,
                        title,
                    )
                    return title
                break

        self._logger.debug(
            "bale_pv resolve_group_title not_found instance=%s peer_id=%s",
            instance,
            peer_id,
        )
        return None

    # ------------------------------------------------------------------
    # Internal WebSocket listener
    # ------------------------------------------------------------------

    async def _start_messaging_client(self, runtime: BalePvInstanceRuntime) -> None:
        """Initialize and connect the messaging WebSocket client."""
        BaleMessagingClient = _get_messaging_client()

        session_file = self._session_path(runtime)
        try:
            jwt_raw = session_file.read_text().strip()
            if jwt_raw.startswith("{"):
                data = json.loads(jwt_raw)
                jwt = data.get("jwt", "")
            else:
                # Strip "jwt:" prefix if present
                jwt = jwt_raw[4:] if jwt_raw.startswith("jwt:") else jwt_raw
        except Exception:
            self._logger.warning(
                "bale_pv no_session_file instance=%s",
                runtime.instance_key,
            )
            return

        metadata = {
            "app_version": "157595",
            "browser_type": "1",
            "browser_version": "148.0.0.0",
            "os_type": "3",
            "session_id": str(int(time.time() * 1000)),
            "mt_app_version": "157595",
            "mt_browser_type": "1",
            "mt_browser_version": "148.0.0.0",
            "mt_os_type": "3",
            "mt_session_id": str(int(time.time() * 1000)),
        }

        # Close old client if reconnecting
        if runtime.client is not None:
            try:
                await runtime.client.close()
            except Exception:
                pass
            runtime.client = None

        client = BaleMessagingClient(
            jwt_token=jwt,
            metadata=metadata,
            update_queue=runtime.message_queue,
        )
        try:
            await client.connect()
            runtime.client = client
            self._logger.info(
                "bale_pv messaging_client_connected instance=%s",
                runtime.instance_key,
            )
            # Start background listener
            runtime.ws_task = asyncio.create_task(
                self._ws_listen(runtime)
            )
        except Exception as exc:
            self._logger.warning(
                "bale_pv messaging_client_connect_failed instance=%s error=%s",
                runtime.instance_key,
                exc,
            )
            runtime.client = None

    # ------------------------------------------------------------------
    # Contacts / Dialogs
    # ------------------------------------------------------------------

    async def get_dialogs(self, instance: str) -> Dict[str, Any]:
        """Fetch dialogs for an authenticated instance.

        Currently returns dialogs from WebSocket updates. In the future,
        this can be enhanced to fetch dialogs via HTTP gRPC-Web.
        """
        runtime = self._get_runtime(instance)
        if runtime.auth_state != "authenticated":
            return {"ok": False, "description": "not_authenticated"}

        # Ensure WebSocket is connected so we can receive dialog updates
        if not runtime.ws_task or runtime.ws_task.done():
            await self._start_messaging_client(runtime)

        # For now, drain any dialog updates from the queue and return them.
        # Dialogs are pushed by the server after the WS handshake via
        # the dialogs.start() flow.
        from bale_grpc_client.update_parser import parse_dialog

        dialogs: List[Dict[str, Any]] = []
        try:
            for _ in range(100):
                raw = runtime.message_queue.get_nowait()
                if not raw or not isinstance(raw, bytes):
                    continue
                dlg = parse_dialog(raw)
                if dlg and dlg.get("peer"):
                    peer = dlg["peer"]
                    dialogs.append({
                        "peer_id": peer.get("id"),
                        "peer_type": peer.get("type", 1),
                        "unread_count": dlg.get("unread_count", 0),
                        "text": dlg.get("text", ""),
                        "date": dlg.get("date"),
                    })
        except asyncio.QueueEmpty:
            pass

        return {"ok": True, "dialogs": dialogs}

    async def sync_bale_dialogs(
        self,
        instance: str,
        *,
        limit: int = 200,
        load_history: bool = False,
        history_limit: int = 50,
    ) -> Dict[str, Any]:
        """Fetch Bale dialogs, user details, and optionally history.

        Returns a dict with:
          - dialogs: list of normalized dialog objects
          - users_by_id: map of uid -> user dict (includes is_bot)
          - groups_by_id: map of group id -> group dict
          - history_by_peer: map of peer key -> list of messages (if load_history)
        """
        import asyncio
        from bale_grpc_client.dialog_parser import (
            parse_load_dialogs_response,
            parse_load_users_response,
            parse_load_history_response,
        )
        from bale_grpc_client.messaging_messages import Peer

        runtime = self._get_runtime(instance)
        if runtime.auth_state != "authenticated":
            return {"ok": False, "description": "not_authenticated"}

        if runtime.client is None:
            return {"ok": False, "description": "messaging_client_not_connected"}

        try:
            raw = await runtime.client.load_dialogs(limit=limit)
        except Exception as exc:
            self._logger.warning("bale_pv load_dialogs_failed instance=%s error=%s", instance, exc)
            return {"ok": False, "description": f"load_dialogs_failed: {exc}"}

        parsed = parse_load_dialogs_response(raw)
        dialogs = parsed.get("dialogs", [])
        users = parsed.get("users", [])
        groups = parsed.get("groups", [])

        self._logger.info(
            "bale_pv load_dialogs ok instance=%s dialogs=%s users=%s groups=%s",
            instance,
            len(dialogs),
            len(users),
            len(groups),
        )

        # Build lookup maps
        users_by_id: Dict[int, Dict[str, Any]] = {}
        groups_by_id: Dict[int, Dict[str, Any]] = {}
        for u in users:
            uid = u.get("id")
            if uid is not None:
                users_by_id[int(uid)] = u
                if u.get("name"):
                    runtime.user_cache[int(uid)] = u["name"]
        for g in groups:
            gid = g.get("id")
            if gid is not None:
                groups_by_id[int(gid)] = g

        # Load user details for any missing users referenced by dialogs
        user_peer_ids = set()
        for d in dialogs:
            peer = d.get("peer") or {}
            if peer.get("type") == Peer.PEER_TYPE_USER:
                uid = peer.get("id")
                if uid is not None and int(uid) not in users_by_id:
                    user_peer_ids.add(int(uid))

        if user_peer_ids:
            try:
                peers = [{"uid": uid} for uid in user_peer_ids]
                users_raw = await runtime.client.load_users(peers)
                extra = parse_load_users_response(users_raw)
                for u in extra.get("users", []):
                    uid = u.get("id")
                    if uid is not None:
                        users_by_id[int(uid)] = u
                self._logger.info(
                    "bale_pv load_users ok instance=%s count=%s",
                    instance,
                    len(extra.get("users", [])),
                )
            except Exception as exc:
                self._logger.warning("bale_pv load_users_failed instance=%s error=%s", instance, exc)

        # Normalize dialogs with display names
        normalized_dialogs: List[Dict[str, Any]] = []
        for d in dialogs:
            peer = d.get("peer") or {}
            peer_type = peer.get("type", Peer.PEER_TYPE_USER)
            peer_id = peer.get("id")
            if peer_id is None:
                continue

            display_name = None
            is_bot = False
            peer_type_label = "user"

            if peer_type == Peer.PEER_TYPE_USER:
                user = users_by_id.get(int(peer_id), {})
                display_name = user.get("name") or user.get("nick") or str(peer_id)
                is_bot = bool(user.get("is_bot"))
                peer_type_label = "bot" if is_bot else "user"
            elif peer_type == Peer.PEER_TYPE_GROUP:
                group = groups_by_id.get(int(peer_id), {})
                display_name = group.get("title") or str(peer_id)
                peer_type_label = "group"
            elif peer_type == Peer.PEER_TYPE_CHANNEL:
                group = groups_by_id.get(int(peer_id), {})
                display_name = group.get("title") or str(peer_id)
                peer_type_label = "channel"

            final_display_name = f"({peer_type_label}) {display_name}" if display_name else f"({peer_type_label}) {peer_id}"
            normalized = {
                "peer_id": int(peer_id),
                "peer_type": int(peer_type),
                "peer_type_label": peer_type_label,
                "display_name": final_display_name,
                "raw_name": display_name,
                "is_bot": is_bot,
                "unread_count": d.get("unread_count", 0),
                "date": d.get("date"),
                "rid": d.get("rid"),
            }
            runtime.chat_title_cache[int(peer_id)] = final_display_name
            normalized_dialogs.append(normalized)

        result: Dict[str, Any] = {
            "ok": True,
            "dialogs": normalized_dialogs,
            "users_by_id": users_by_id,
            "groups_by_id": groups_by_id,
        }

        # Optionally load recent history for each dialog
        if load_history:
            history_by_peer: Dict[str, List[Dict[str, Any]]] = {}
            for dlg in normalized_dialogs:
                peer_key = f"{dlg['peer_type']}|{dlg['peer_id']}"
                try:
                    raw_hist = await runtime.client.load_history(
                        peer_id=dlg["peer_id"],
                        peer_type=dlg["peer_type"],
                        limit=history_limit,
                    )
                    hist = parse_load_history_response(raw_hist)
                    messages = hist.get("history", [])
                    # Enrich sender names for group messages
                    for msg in messages:
                        sender_uid = msg.get("sender_uid")
                        if sender_uid is not None:
                            sender = users_by_id.get(int(sender_uid), {})
                            msg["sender_name"] = sender.get("name") or sender.get("nick") or str(sender_uid)
                            msg["sender_is_bot"] = bool(sender.get("is_bot"))
                    history_by_peer[peer_key] = messages
                    # Small delay to avoid hammering the server
                    await asyncio.sleep(0.3)
                except Exception as exc:
                    self._logger.warning(
                        "bale_pv load_history_failed instance=%s peer=%s error=%s",
                        instance,
                        peer_key,
                        exc,
                    )
                    history_by_peer[peer_key] = []
            result["history_by_peer"] = history_by_peer

        return result

    async def get_contacts(self, instance: str) -> Dict[str, Any]:
        """Fetch contacts list for an authenticated instance.

        Uses HTTP gRPC-Web after establishing session cookie.
        """
        import httpx
        from bale_grpc_client.protobuf_wire import (
            ProtobufMessage,
            ProtobufParser,
            grpc_web_frame,
            parse_grpc_web_response,
        )

        runtime = self._get_runtime(instance)
        if runtime.auth_state != "authenticated":
            return {"ok": False, "description": "not_authenticated"}

        session_file = self._session_path(runtime)
        try:
            jwt_raw = session_file.read_text().strip()
            if jwt_raw.startswith("{"):
                data = json.loads(jwt_raw)
                jwt = data.get("jwt", "")
            else:
                jwt = jwt_raw[4:] if jwt_raw.startswith("jwt:") else jwt_raw
        except Exception:
            return {"ok": False, "description": "no_session_file"}

        try:
            async with httpx.AsyncClient() as client:
                # Establish cookie session
                resp = await client.post(
                    "https://next-ws.bale.ai/set-cookie/",
                    headers={
                        "Authorization": f"Bearer {jwt}",
                        "Origin": "https://web.bale.ai",
                    },
                )
                if resp.status_code != 200:
                    return {"ok": False, "description": "set_cookie_failed"}

                # Fetch contacts
                req = ProtobufMessage()
                req.add_string(1, "")  # contactsHash
                resp = await client.post(
                    "https://next-ws.bale.ai/bale.users.v1.Users/GetContacts",
                    content=grpc_web_frame(req.serialize()),
                    headers={
                        "content-type": "application/grpc-web+proto",
                        "x-grpc-web": "1",
                        "mt_app_version": "157595",
                        "app_version": "157595",
                        "browser_type": "1",
                        "mt_browser_type": "1",
                        "browser_version": "148.0.0.0",
                        "mt_browser_version": "148.0.0.0",
                        "os_type": "3",
                        "mt_os_type": "3",
                        "session_id": str(int(time.time() * 1000)),
                        "mt_session_id": str(int(time.time() * 1000)),
                    },
                )

                msg, status, _ = parse_grpc_web_response(resp.content)
                if status != 0:
                    return {
                        "ok": False,
                        "description": f"grpc_error_{status}",
                    }

                fields = ProtobufParser(msg).parse()
                user_ids: List[int] = []
                user_bytes_list = fields.get(3, [])
                for ub in user_bytes_list:
                    uf = ProtobufParser(ub).parse()
                    uid = uf.get(1, [None])[0]
                    if isinstance(uid, int):
                        user_ids.append(uid)

                # Fetch names via LoadUsers (field 4 contains the display name)
                name_map: Dict[int, str] = {}
                if user_ids:
                    lu_req = ProtobufMessage()
                    for uid in user_ids:
                        uid_msg = ProtobufMessage()
                        uid_msg.add_int64(1, uid)
                        lu_req.add_message(1, uid_msg)
                    lu_resp = await client.post(
                        "https://next-ws.bale.ai/bale.users.v1.Users/LoadUsers",
                        content=grpc_web_frame(lu_req.serialize()),
                        headers={
                            "content-type": "application/grpc-web+proto",
                            "x-grpc-web": "1",
                            "mt_app_version": "157595",
                            "app_version": "157595",
                            "browser_type": "1",
                            "mt_browser_type": "1",
                            "browser_version": "148.0.0.0",
                            "mt_browser_version": "148.0.0.0",
                            "os_type": "3",
                            "mt_os_type": "3",
                            "session_id": str(int(time.time() * 1000)),
                            "mt_session_id": str(int(time.time() * 1000)),
                        },
                    )
                    lu_msg, lu_status, _ = parse_grpc_web_response(lu_resp.content)
                    if lu_status == 0 and lu_msg:
                        lu_fields = ProtobufParser(lu_msg).parse()
                        for user_bytes in lu_fields.get(1, []):
                            if not isinstance(user_bytes, bytes):
                                continue
                            uuf = ProtobufParser(user_bytes).parse()
                            uid = uuf.get(1, [None])[0]
                            name_bytes = uuf.get(4, [None])[0]
                            display_name = ""
                            if isinstance(name_bytes, bytes):
                                try:
                                    nested = ProtobufParser(name_bytes).parse()
                                    name_val = nested.get(1, [None])[0]
                                    if isinstance(name_val, bytes):
                                        display_name = name_val.decode("utf-8", errors="replace")
                                except Exception:
                                    pass
                            if isinstance(uid, int):
                                name_map[uid] = display_name

                contacts = []
                for uid in user_ids:
                    contacts.append({
                        "id": uid,
                        "name": name_map.get(uid, ""),
                    })

                self._logger.info(
                    "bale_pv get_contacts ok instance=%s count=%s names=%s",
                    instance,
                    len(contacts),
                    sum(1 for c in contacts if c["name"]),
                )
                return {"ok": True, "contacts": contacts}
        except Exception as exc:
            self._logger.exception("bale_pv get_contacts error")
            return {"ok": False, "description": str(exc)}

    async def _refresh_user_cache(self, runtime: BalePvInstanceRuntime) -> None:
        """Fetch contacts and populate the user_cache for name lookups."""
        result = await self.get_contacts(runtime.instance_key)
        if not result.get("ok"):
            self._logger.warning(
                "bale_pv user_cache_refresh_failed instance=%s error=%s",
                runtime.instance_key,
                result.get("description"),
            )
            return
        contacts = result.get("contacts") or []
        before = len(runtime.user_cache)
        for contact in contacts:
            uid = contact.get("id")
            if uid is None:
                continue
            name = contact.get("name") or ""
            if name:
                runtime.user_cache[int(uid)] = str(name).strip()
                runtime.chat_title_cache[int(uid)] = str(name).strip()

        # Also refresh group/channel title cache so group conversations are
        # named correctly in Chatwoot.
        try:
            dialogs_result = await self.sync_bale_dialogs(
                runtime.instance_key,
                load_history=False,
            )
            if not dialogs_result.get("ok"):
                self._logger.debug(
                    "bale_pv refresh_dialogs_skipped instance=%s reason=%s",
                    runtime.instance_key,
                    dialogs_result.get("description"),
                )
        except Exception as exc:
            self._logger.debug(
                "bale_pv refresh_dialogs_failed instance=%s error=%s",
                runtime.instance_key,
                exc,
            )

        runtime.last_user_cache_refresh = time.time()
        self._logger.info(
            "bale_pv user_cache_refreshed instance=%s before=%s after=%s",
            runtime.instance_key,
            before,
            len(runtime.user_cache),
        )

    async def _ws_listen(self, runtime: BalePvInstanceRuntime) -> None:
        """Background WebSocket listener — keeps connection alive and reconnects."""
        self._logger.info(
            "bale_pv ws_listen_started instance=%s",
            runtime.instance_key,
        )
        try:
            while not runtime.stop_event.is_set():
                try:
                    # Check if the WebSocket client is still connected
                    connected = False
                    try:
                        connected = runtime.client is not None and runtime.client.ws.is_connected
                    except Exception as exc:
                        self._logger.debug(
                            "bale_pv ws_listen_is_connected_error instance=%s error=%s",
                            runtime.instance_key,
                            exc,
                        )
                    if not connected:
                        self._logger.warning(
                            "bale_pv ws_reconnecting instance=%s",
                            runtime.instance_key,
                        )
                        try:
                            await self._start_messaging_client(runtime)
                        except Exception as exc:
                            self._logger.warning(
                                "bale_pv ws_reconnect_failed instance=%s error=%s",
                                runtime.instance_key,
                                exc,
                            )
                            await asyncio.sleep(5)
                            continue
                    await asyncio.sleep(2)
                except Exception as exc:
                    self._logger.warning(
                        "bale_pv ws_listen_loop_error instance=%s error=%s",
                        runtime.instance_key,
                        exc,
                    )
                    await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._logger.error(
                "bale_pv ws_listen_fatal instance=%s error=%s exc_type=%s",
                runtime.instance_key,
                exc,
                type(exc).__name__,
                exc_info=True,
            )
        finally:
            self._logger.info(
                "bale_pv ws_listen_stopped instance=%s",
                runtime.instance_key,
            )

    def _start_websocket_listener(self, runtime: BalePvInstanceRuntime) -> None:
        """Schedule the WebSocket keep-alive listener task if not already running.

        The listener reconnects automatically when the underlying WS drops.
        Calling this method while a healthy task is already running is a no-op.
        """
        if runtime.ws_task and not runtime.ws_task.done():
            return
        runtime.ws_task = asyncio.create_task(self._ws_listen(runtime))

    def _message_to_event_dict(self, message: Any) -> Dict[str, Any]:
        """Convert a Balethon Message object to a Bot-API-style update dict.

        Used only for the legacy Balethon code path.  The gRPC-Web path builds
        update dicts directly in _parse_raw_update.
        """
        chat_id = str(message.chat.id) if message.chat else ""
        author = message.author
        from_dict: Dict[str, Any] = {}
        if author:
            from_dict = {
                "id": getattr(author, "id", None),
                "first_name": getattr(author, "first_name", None) or "",
                "last_name": getattr(author, "last_name", None) or "",
                "username": getattr(author, "username", None),
            }

        chat_dict = {"id": chat_id, "type": "private"}

        msg_dict: Dict[str, Any] = {
            "message_id": str(message.id) if message.id else None,
            "date": int(message.date.timestamp()) if hasattr(message.date, "timestamp") else 0,
            "chat": chat_dict,
            "from": from_dict,
            "text": message.text,
        }

        if message.caption:
            msg_dict["caption"] = message.caption

        if message.reply_to_message:
            msg_dict["reply_to_message"] = {
                "message_id": str(message.reply_to_message.id),
            }

        # Basic media extraction (placeholders — the gRPC path handles real media).
        if message.photo:
            msg_dict["photo"] = [{"file_id": "photo_placeholder"}]
        if message.document:
            msg_dict["document"] = {
                "file_id": "document_placeholder",
                "file_name": getattr(message.document, "file_name", "file"),
                "mime_type": getattr(message.document, "mime_type", "application/octet-stream"),
            }
        if message.voice:
            msg_dict["voice"] = {"file_id": "voice_placeholder"}
        if message.video:
            msg_dict["video"] = {"file_id": "video_placeholder"}

        return {
            "update_id": (
                int(message.id.split(":")[0])
                if message.id and ":" in str(message.id)
                else 0
            ),
            "message": msg_dict,
        }


# Module-level singleton — imported throughout the application.
bale_pv = BalePvConnector()
