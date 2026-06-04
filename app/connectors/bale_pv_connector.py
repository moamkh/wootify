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

    def _session_path(self, runtime: BalePvInstanceRuntime) -> Path:
        runtime.session_dir.mkdir(parents=True, exist_ok=True)
        return runtime.session_dir / f"{runtime.phone_number}.session"

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
        phone_number = str(params.get("bale_pv_phone_number") or "").strip()
        if not phone_number:
            raise RuntimeError(f"Bale PV instance '{instance}' missing bale_pv_phone_number")

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

        if self._has_valid_session(runtime):
            runtime.auth_state = "authenticated"
            self._logger.info(
                "bale_pv session_exists instance=%s phone=%s",
                instance,
                phone_number,
            )
            # Attempt to start messaging client (may fail if JWT expired)
            await self._start_messaging_client(runtime)
        else:
            self._logger.info(
                "bale_pv awaiting_auth instance=%s phone=%s",
                instance,
                phone_number,
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
            if quoted and quoted.get("message_id"):
                reply_to = int(quoted["message_id"])

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
            return {"ok": True, "result": {"raw_response": response.hex()}}
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
        """Send media via the userbot."""
        runtime = self._get_runtime(instance)
        if runtime.auth_state != "authenticated":
            raise RuntimeError(f"Instance {instance} is not authenticated")

        self._logger.warning(
            "bale_pv send_media_not_implemented instance=%s",
            instance,
        )
        raise RuntimeError(
            "Bale PV userbot send_media is not yet implemented. "
            "Media upload requires file service protobuf messages."
        )

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

        updates: List[Dict[str, Any]] = []
        max_items = 50
        wait_seconds = min(timeout or 1, 5)

        try:
            # Wait for at least one raw protobuf update, then drain the queue
            raw = await asyncio.wait_for(runtime.message_queue.get(), timeout=wait_seconds)
            if raw:
                parsed = self._parse_raw_update(raw)
                if parsed:
                    updates.append(parsed)
            # Drain remaining without blocking
            for _ in range(max_items - 1):
                try:
                    raw = runtime.message_queue.get_nowait()
                    if raw:
                        parsed = self._parse_raw_update(raw)
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
    def _parse_raw_update(raw: Any) -> Optional[Dict[str, Any]]:
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

        text = parsed.get("text") or ""
        rid = parsed.get("rid")
        date = parsed.get("date")
        media = parsed.get("media")
        peer = parsed.get("peer") or {}
        peer_id = peer.get("id") or sender_uid

        # For private chats, chat_id = sender_uid
        chat_id = str(sender_uid)

        message: Dict[str, Any] = {
            "message_id": str(rid) if rid else None,
            "date": int(date or 0),
            "chat": {"id": chat_id, "type": "private"},
            "from": {
                "id": sender_uid,
                "first_name": str(chat_id),
            },
            "text": text,
        }

        # Attach media metadata for downstream processing
        if media:
            # Store composite file_id so download_file_by_id can reconstruct
            # the GetNasimFileUrl request (needs file_id + access_hash + peer_id)
            composite = {
                "file_id": media.get("file_id"),
                "access_hash": media.get("access_hash"),
                "peer_id": peer_id,
                "file_name": media.get("file_name", ""),
            }
            media["file_id"] = json.dumps(composite, separators=(",", ":"))
            message["document"] = media
            message["mime_type"] = media.get("mime_type", "")
            message["file_name"] = media.get("file_name", "")
            # Add image dimensions if available
            if "width" in media:
                message["width"] = media["width"]
            if "height" in media:
                message["height"] = media["height"]

        return {
            "update_id": int(rid or 0),
            "message": message,
        }

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
            return b"", None, None

        # Parse composite file_id
        try:
            file_info = json.loads(file_id)
            fid = int(file_info["file_id"])
            ahash = int(file_info["access_hash"])
            peer_id = int(file_info["peer_id"])
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
                ProtobufMessage,
                ProtobufParser,
                grpc_web_frame,
                parse_grpc_web_response,
            )

            session_file = self._session_path(runtime)
            jwt_raw = session_file.read_text().strip()
            if jwt_raw.startswith("{"):
                jwt = json.loads(jwt_raw).get("jwt", "")
            else:
                jwt = jwt_raw[4:] if jwt_raw.startswith("jwt:") else jwt_raw

            async with httpx.AsyncClient() as client:
                # Establish cookie session
                cookie_resp = await client.post(
                    "https://next-ws.bale.ai/set-cookie/",
                    headers={
                        "Authorization": f"Bearer {jwt}",
                        "Origin": "https://web.bale.ai",
                    },
                )
                if cookie_resp.status_code != 200:
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

                msg, status, _ = parse_grpc_web_response(resp.content)
                if status != 0:
                    self._logger.warning(
                        "bale_pv GetNasimFileUrl grpc_error instance=%s status=%s",
                        instance,
                        status,
                    )
                    return b"", None, None

                fields = ProtobufParser(msg).parse()
                file_url_bytes = fields.get(1, [None])[0]
                if not file_url_bytes or not isinstance(file_url_bytes, bytes):
                    return b"", None, None

                url_fields = ProtobufParser(file_url_bytes).parse()
                url_val = url_fields.get(2, [b""])[0]
                if not url_val or not isinstance(url_val, bytes):
                    return b"", None, None

                download_url = url_val.decode("utf-8")
                self._logger.info(
                    "bale_pv downloading file instance=%s url=%s",
                    instance,
                    download_url[:80],
                )

                # Download the actual file
                file_resp = await client.get(download_url)
                if file_resp.status_code != 200:
                    return b"", None, None

                content_type = file_resp.headers.get("content-type")
                return file_resp.content, content_type, filename or None

        except Exception as exc:
            self._logger.exception(
                "bale_pv download_file_by_id error instance=%s",
                instance,
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
                contacts = []
                user_bytes_list = fields.get(3, [])
                for ub in user_bytes_list:
                    uf = ProtobufParser(ub).parse()
                    uid = uf.get(1, [None])[0]
                    # GetContacts only returns IDs; names require LoadUsers call
                    name_val = uf.get(2, [None])[0]
                    nick_val = uf.get(5, [None])[0]
                    name = ""
                    nick = ""
                    if isinstance(name_val, bytes):
                        name = name_val.decode("utf-8", errors="replace")
                    if isinstance(nick_val, bytes):
                        nick = nick_val.decode("utf-8", errors="replace")
                    contacts.append({
                        "id": uid,
                        "name": name,
                        "nick": nick,
                    })

                return {"ok": True, "contacts": contacts}
        except Exception as exc:
            self._logger.exception("bale_pv get_contacts error")
            return {"ok": False, "description": str(exc)}

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
