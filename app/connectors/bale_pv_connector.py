"""
Module Overview
---------------
Purpose: Bale personal-account (userbot) connector using Balethon over WebSocket.
Documentation Standard: module/class/public-method docstrings.
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

# Ensure bale_grpc_client is on path
_bale_grpc_client_path = str(Path(__file__).resolve().parent.parent.parent / "bale_grpc_client")
if _bale_grpc_client_path not in sys.path:
    sys.path.insert(0, _bale_grpc_client_path)

def _get_messaging_client():
    """Lazy import BaleMessagingClient to avoid circular imports."""
    from bale_grpc_client.messaging_client import BaleMessagingClient
    return BaleMessagingClient

logger = logging.getLogger("app.connectors.bale_pv")

# Ensure bale_grpc_client is on path
_bale_grpc_client_path = str(Path(__file__).resolve().parent.parent.parent / "bale_grpc_client")
if _bale_grpc_client_path not in sys.path:
    sys.path.insert(0, _bale_grpc_client_path)

def _get_auth_client():
    """Lazy import BaleAuthClient to avoid circular imports."""
    from bale_grpc_client.auth_client import BaleAuthClient
    from bale_grpc_client.exceptions import BaleAuthError
    return BaleAuthClient, BaleAuthError

def _get_messaging_client():
    """Lazy import BaleMessagingClient to avoid circular imports."""
    from bale_grpc_client.messaging_client import BaleMessagingClient
    return BaleMessagingClient


@dataclass
class BalePvInstanceRuntime:
    """Runtime state for a bale-pv instance."""

    instance_key: str
    phone_number: str
    client: Any = None
    message_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    ws_task: Optional[asyncio.Task] = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    session_dir: Path = field(default_factory=lambda: Path("./data/bale_pv_sessions"))
    auth_state: str = "unauthenticated"  # unauthenticated | code_sent | authenticated
    transaction_hash: Optional[str] = None
    session_id: Optional[str] = None  # UUID-based session identifier
    user_cache: Dict[int, str] = field(default_factory=dict)  # uid -> display name
    self_user_id: Optional[int] = None  # extracted from JWT payload
    last_user_cache_refresh: float = 0.0  # unix timestamp


class BalePvConnector:
    """Connector for Bale personal accounts using Balethon userbot mode."""

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

    async def send_media(
        self,
        instance: str,
        chat_id: str,
        media_url_or_bytes: Any,
        filename: str,
        caption: Optional[str] = None,
        quoted: Optional[Dict] = None,
        reply_markup: Any = None,
    ) -> Dict:
        """Send media via the userbot.

        Tries to upload the file to Bale's Nasim storage and send it as a
        DocumentMessage. Falls back to a text message with the attachment
        URL if upload fails.
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
            # Download from URL
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
                        self._logger.warning(
                            "bale_pv media_download_failed instance=%s url=%s status=%s body=%s",
                            instance,
                            media_url_or_bytes[:120],
                            resp.status_code,
                            resp.text[:200],
                        )
            except Exception as exc:
                self._logger.warning(
                    "bale_pv media_download_error instance=%s url=%s error=%s",
                    instance,
                    media_url_or_bytes[:120],
                    exc,
                )

        if file_bytes:
            try:
                uploaded = await self._upload_file_to_nasim(
                    instance, chat_id, file_bytes, filename, caption, quoted
                )
                if uploaded:
                    return uploaded
            except Exception as exc:
                self._logger.warning(
                    "bale_pv media_upload_failed instance=%s error=%s",
                    instance,
                    exc,
                )

        # Fallback: send text with attachment info
        self._logger.warning(
            "bale_pv send_media_fallback instance=%s chat_id=%s filename=%s",
            instance,
            chat_id,
            filename,
        )
        parts: List[str] = []
        if caption:
            parts.append(str(caption))
        if isinstance(media_url_or_bytes, str):
            parts.append(f"[Attachment: {filename}]\n{media_url_or_bytes}")
        else:
            parts.append(f"[Attachment: {filename}]")
        fallback_text = "\n".join(parts)

        result = await self.send_text(
            instance,
            chat_id,
            fallback_text,
            quoted=quoted,
        )
        result["_media_fallback"] = True
        result["_original_filename"] = filename
        return result

    async def _upload_file_to_nasim(
        self,
        instance: str,
        chat_id: str,
        file_bytes: bytes,
        filename: str,
        caption: Optional[str] = None,
        quoted: Optional[Dict] = None,
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

        # Resolve mime type
        import mimetypes
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        # Build upload URL request
        req = GetNasimFileUploadUrlRequest(
            expected_size=len(file_bytes),
            name=filename,
            mime_type=mime_type,
            uid=peer_id,
            send_type=SendTypeValue.SEND_TYPE_DOCUMENT,
            peer_type=Peer.PEER_TYPE_USER,
            access_hash=0,
        )

        session_file = self._session_path(runtime)
        jwt_raw = session_file.read_text().strip()
        if jwt_raw.startswith("{"):
            jwt = json.loads(jwt_raw).get("jwt", "")
        else:
            jwt = jwt_raw[4:] if jwt_raw.startswith("jwt:") else jwt_raw

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
                return None

            # Call GetNasimFileUploadUrl
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

            msg, status, grpc_msg = parse_grpc_web_response(resp.content)
            if status != 0:
                self._logger.warning(
                    "bale_pv GetNasimFileUploadUrl grpc_error instance=%s status=%s msg=%s",
                    instance,
                    status,
                    grpc_msg,
                )
                return None

            # Parse upload URL response
            fields = ProtobufParser(msg).parse()
            upload_url = ""
            file_id = 0
            chunk_size = len(file_bytes)
            self._logger.info("bale_pv upload_url_response fields=%s", fields)
            for key, vals in fields.items():
                if key == 2 and vals:
                    val = vals[0]
                    if isinstance(val, bytes):
                        val = val.decode("utf-8", errors="replace")
                    if isinstance(val, str) and val.startswith("http"):
                        upload_url = val
                elif key == 1 and vals and isinstance(vals[0], int):
                    file_id = vals[0]
                elif key == 4 and vals and isinstance(vals[0], int):
                    chunk_size = vals[0]

            if not upload_url:
                self._logger.warning(
                    "bale_pv GetNasimFileUploadUrl no_url instance=%s msg_len=%s fields=%s",
                    instance,
                    len(msg),
                    fields,
                )
                return None
            if not file_id:
                self._logger.warning(
                    "bale_pv GetNasimFileUploadUrl no_file_id instance=%s fields=%s",
                    instance,
                    fields,
                )
                return None

            self._logger.info(
                "bale_pv uploading file instance=%s url=%s size=%s file_id=%s chunk_size=%s",
                instance,
                upload_url[:120],
                len(file_bytes),
                file_id,
                chunk_size,
            )

            # Upload file bytes via PUT (matching Balethon behaviour)
            upload_resp = await client.put(
                upload_url,
                content=file_bytes,
            )
            if upload_resp.status_code not in (200, 201, 204):
                self._logger.warning(
                    "bale_pv file_upload_failed instance=%s status=%s url=%s body=%s",
                    instance,
                    upload_resp.status_code,
                    upload_url[:80],
                    upload_resp.text[:200],
                )
                return None

            self._logger.info(
                "bale_pv file_uploaded instance=%s file_id=%s size=%s",
                instance,
                file_id,
                len(file_bytes),
            )

            # Send document message via WebSocket
            reply_to = None
            if quoted:
                reply_to_val = quoted.get("message_id") or quoted.get("id")
                if reply_to_val is not None:
                    reply_to = int(reply_to_val)

            await runtime.client.send_document(
                peer_id=peer_id,
                file_id=file_id,
                access_hash=peer_id,
                file_size=len(file_bytes),
                name=filename,
                mime_type=mime_type,
                caption=caption or None,
                reply_to_message_id=reply_to,
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

        if runtime.auth_state != "authenticated":
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

        try:
            # Wait for at least one raw protobuf update, then drain the queue
            raw = await asyncio.wait_for(runtime.message_queue.get(), timeout=wait_seconds)
            if raw:
                parsed = self._parse_raw_update(raw, runtime.user_cache, runtime.self_user_id)
                if parsed:
                    updates.append(parsed)
            # Drain remaining without blocking
            for _ in range(max_items - 1):
                try:
                    raw = runtime.message_queue.get_nowait()
                    if raw:
                        parsed = self._parse_raw_update(raw, runtime.user_cache, runtime.self_user_id)
                        if parsed:
                            updates.append(parsed)
                except asyncio.QueueEmpty:
                    break
        except asyncio.TimeoutError:
            pass

        return {
            "ok": True,
            "result": updates,
        }

    @staticmethod
    def _parse_raw_update(raw: Any, user_cache: Optional[Dict[int, str]] = None, self_user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Parse a raw protobuf update into a Bot-API-style update dict."""
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

        peer = parsed.get("peer") or {}
        peer_id = peer.get("id") or sender_uid
        peer_type = peer.get("type", 1)

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

        # Look up sender name from user cache (populated from contacts)
        cached_name = ""
        if user_cache and isinstance(sender_uid, int):
            cached_name = user_cache.get(sender_uid, "")

        text = parsed.get("text") or ""

        # For group/channel messages, prefix with sender name so members are distinguishable
        if peer_type in (2, 3) and not is_outgoing and text:
            sender_label = cached_name or str(sender_uid)
            text = f"{sender_label}: {text}"

        rid = parsed.get("rid")
        date = parsed.get("date")
        media = parsed.get("media")

        message: Dict[str, Any] = {
            "message_id": str(rid) if rid else None,
            "date": int(date or 0),
            "chat": {"id": chat_id, "type": "private"},
            "from": {
                "id": sender_uid,
                "first_name": cached_name,
                "username": cached_name,
            },
            "text": text,
        }
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
                if mime == "image/webp" or file_name.endswith(".webp"):
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
                        "mime_type": media.get("mime_type", ""),
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
            ahash = int(file_info["access_hash"])
            filename = file_info.get("file_name", "")
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            self._logger.warning(
                "bale_pv download_file_by_id invalid file_id format instance=%s file_id=%s error=%s",
                instance,
                file_id,
                exc,
            )
            return b"", None, None

        try:
            import httpx
            from bale_grpc_client.messaging_messages import GetNasimFileUrlRequest
            from bale_grpc_client.protobuf_wire import (
                grpc_web_frame,
                parse_grpc_web_response,
            )

            session_file = self._session_path(runtime)
            jwt_raw = session_file.read_text().strip()
            if jwt_raw.startswith("{"):
                jwt = json.loads(jwt_raw).get("jwt", "")
            else:
                jwt = jwt_raw[4:] if jwt_raw.startswith("jwt:") else jwt_raw

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

                req = GetNasimFileUrlRequest(
                    file_id=fid,
                    access_hash=ahash,
                )
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
                    return b"", None, None

                download_url = self._extract_url_from_nasim_response(msg)
                if not download_url:
                    self._logger.warning(
                        "bale_pv GetNasimFileUrl no_url instance=%s msg_len=%s fields=%s",
                        instance,
                        len(msg),
                        msg[:32].hex() if msg else "empty",
                    )
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

            normalized = {
                "peer_id": int(peer_id),
                "peer_type": int(peer_type),
                "peer_type_label": peer_type_label,
                "display_name": f"({peer_type_label}) {display_name}" if display_name else f"({peer_type_label}) {peer_id}",
                "raw_name": display_name,
                "is_bot": is_bot,
                "unread_count": d.get("unread_count", 0),
                "date": d.get("date"),
                "rid": d.get("rid"),
            }
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
        """Restart WS listener if needed."""
        if runtime.ws_task and not runtime.ws_task.done():
            return
        runtime.ws_task = asyncio.create_task(
            self._ws_listen(runtime)
        )

    def _message_to_event_dict(self, message: Any) -> Dict[str, Any]:
        """Convert a Balethon Message object to a Bot-API-style update dict."""
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

        chat_dict = {
            "id": chat_id,
            "type": "private",
        }

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

        # Media extraction (basic)
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
            "update_id": int(message.id.split(":")[0]) if message.id and ":" in str(message.id) else 0,
            "message": msg_dict,
        }


bale_pv = BalePvConnector()
