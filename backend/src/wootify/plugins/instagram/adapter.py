"""Instagram PV adapter for the Chatwoot bridge.

Wraps ``InstagramPvConnector`` and converts its Bot-API-style updates into the
normalized event shape used by ``ChatwootBridgeService``. Mirrors the Bale PV
adapter contract so bridge/polling code can treat both platforms uniformly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from wootify.adapters.base import BasePlatformAdapter
from wootify.plugins.bale_pv.adapter import BalePvAdapter
from wootify.plugins.instagram.connector import instagram_pv

logger = logging.getLogger("app.instagram.adapter")


class InstagramPvAdapter(BasePlatformAdapter):
    """Adapter that normalizes Instagram PV (userbot) traffic for Chatwoot."""

    def __init__(self, instance_key: str, config: Dict[str, Any]) -> None:
        super().__init__(instance_key, config)
        self._connected = False
        self._self_id: Optional[str] = None

    async def connect(self) -> None:
        username = str(self.config.get("instagram_username") or "").strip()
        password = str(self.config.get("instagram_password") or "").strip()
        if not username or not password:
            raise RuntimeError(
                f"Instagram PV instance '{self.instance_key}' missing username/password"
            )
        await instagram_pv.connect(
            self.instance_key,
            {
                "instagram_username": username,
                "instagram_password": password,
                "instagram_sessionid": self.config.get("instagram_sessionid"),
                "instagram_session_dir": self.config.get("instagram_session_dir"),
                "instagram_verification_code": self.config.get("instagram_verification_code"),
                "instagram_totp_seed": self.config.get("instagram_totp_seed"),
            },
        )
        self._connected = True
        self._self_id = instagram_pv.get_self_user_id(self.instance_key)
        logger.info(
            "instagram_pv_adapter_connected instance=%s self_id=%s",
            self.instance_key,
            self._self_id,
        )

    async def disconnect(self) -> None:
        try:
            await instagram_pv.disconnect(self.instance_key)
        except Exception as exc:
            logger.debug(
                "instagram_pv_adapter_disconnect_error instance=%s error=%s",
                self.instance_key,
                exc,
            )
        self._connected = False

    async def send_text(
        self,
        peer_id: str,
        text: str,
        *,
        reply_to: Optional[str] = None,
        mirror_echo: bool = True,
    ) -> Dict[str, Any]:
        # mirror_echo is a Bale-PV-only concern (Bale never echoes own-session
        # sends); Instagram pushes its own echoes, so it is ignored here.
        del mirror_echo
        quoted: Optional[Dict[str, Any]] = None
        if reply_to:
            quoted = {"message_id": reply_to}
        result = await instagram_pv.send_text(
            self.instance_key,
            peer_id,
            text,
            quoted=quoted,
        )
        return {"ok": True, "result": result}

    async def edit_message(
        self,
        peer_id: str,
        message_id: str,
        text: str,
    ) -> Dict[str, Any]:
        """Instagram DMs do not support editing; always fails."""
        del peer_id, message_id, text
        raise RuntimeError("Instagram DMs do not support message editing")

    async def send_media(
        self,
        peer_id: str,
        media: Any,
        *,
        filename: Optional[str] = None,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        mirror_echo: bool = True,
    ) -> Dict[str, Any]:
        # See send_text: mirror_echo is Bale-PV-only and ignored here.
        del mirror_echo
        quoted: Optional[Dict[str, Any]] = None
        if reply_to:
            quoted = {"message_id": reply_to}

        file_bytes: Optional[bytes] = None
        if isinstance(media, bytes):
            file_bytes = media
        elif isinstance(media, str) and media.startswith(("http://", "https://")):
            file_bytes = await self._download_url(media)

        if not file_bytes:
            raise RuntimeError("No media bytes available to send")

        result = await instagram_pv.send_media(
            self.instance_key,
            peer_id,
            file_bytes,
            filename or "file",
            caption=caption or None,
            quoted=quoted,
        )
        return {"ok": True, "result": result}

    async def poll_events(self) -> AsyncIterator[Dict[str, Any]]:
        while self._connected:
            try:
                updates = await instagram_pv.get_updates(self.instance_key, timeout=5)
            except Exception as exc:
                logger.warning(
                    "instagram_pv_adapter_poll_error instance=%s error=%s",
                    self.instance_key,
                    exc,
                )
                await asyncio.sleep(2)
                continue

            if not isinstance(updates, dict) or not updates.get("ok"):
                await asyncio.sleep(0.5)
                continue

            for raw in updates.get("result", []):
                event = self.normalize_incoming_update(raw)
                if not event:
                    continue

                if event.get("attachments"):
                    try:
                        event["attachments"] = await self.resolve_attachments(
                            event["attachments"]
                        )
                    except Exception as exc:
                        logger.warning(
                            "instagram_pv_adapter_attachments_failed instance=%s message_id=%s error=%s",
                            self.instance_key,
                            event.get("message_id"),
                            exc,
                        )
                        event["attachments"] = []

                yield event

            # This standalone generator owns its deliveries, so it confirms
            # the staged watermarks itself. The polling service instead commits
            # explicitly after bridge delivery (at-least-once semantics).
            await instagram_pv.commit_updates(self.instance_key)
            await asyncio.sleep(0.1)

    def normalize_incoming_update(self, raw_update: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(raw_update, dict):
            return None

        message = raw_update.get("message")
        if not isinstance(message, dict):
            return None

        chat = message.get("chat") or {}
        chat_id = str(chat.get("id") or "")
        chat_type = str(chat.get("type") or "private").strip().lower() or "private"
        if not chat_id:
            return None

        sender = message.get("from") or {}
        sender_id = sender.get("id")
        sender_name = str(sender.get("first_name") or "").strip()
        sender_username = str(sender.get("username") or "").strip()

        is_outgoing = bool(message.get("_outgoing"))

        if chat_type in ("group", "channel"):
            from_name = (
                str(chat.get("title") or "").strip()
                or f"Instagram Group {chat_id}"
            )
        elif is_outgoing:
            # Outgoing echo: the Chatwoot contact is the recipient, not us.
            self_uid = instagram_pv.get_self_user_id(self.instance_key)
            if self_uid is not None and chat_id == str(self_uid):
                logger.debug(
                    "instagram_pv_adapter_skip_self_echo instance=%s chat_id=%s",
                    self.instance_key,
                    chat_id,
                )
                return None
            recipient_name = (
                instagram_pv.get_user_name(self.instance_key, int(chat_id))
                if chat_id.isdigit()
                else None
            )
            from_name = recipient_name or f"Instagram User {chat_id}"
        else:
            from_name = sender_name or sender_username or f"Instagram User {chat_id}"

        text = str(message.get("text") or message.get("caption") or "").strip()

        attachments = self._extract_attachment_refs(message)

        event: Dict[str, Any] = {
            "chat_id": chat_id,
            "chat_type": chat_type,
            "from_name": from_name,
            "text": text,
            "message_id": str(message.get("message_id") or raw_update.get("update_id") or ""),
            "platform_message_id": str(
                message.get("message_id") or raw_update.get("update_id") or ""
            ),
            "sender_id": str(sender_id) if sender_id is not None else None,
            "sender_username": sender_username or None,
            "attachments": attachments,
            "contact": None,
            "reply_to": None,
            "outgoing": is_outgoing,
            "edited": False,
            "raw": raw_update,
        }

        # For group threads, expose the actual sender as a separate contact so
        # agents can open a private 1-on-1 conversation with them.
        if chat_type in ("group", "channel") and sender_id is not None:
            sender_contact_name = (
                sender_name or sender_username or f"Instagram User {sender_id}"
            )
            event["sender_contact"] = {
                "identifier": f"INSTAGRAM_PV:{sender_id}",
                "name": sender_contact_name,
                "phone_number": None,
                "username": sender_username or None,
            }

        return event

    def get_self_id(self) -> Optional[str]:
        return self._self_id

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _download_url(self, url: str) -> bytes:
        import httpx

        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

    async def resolve_attachments(
        self, attachments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        resolved: List[Dict[str, Any]] = []
        for att in attachments:
            file_id = att.get("file_id")
            if not file_id:
                continue
            try:
                content, content_type, file_path = await instagram_pv.download_file_by_id(
                    self.instance_key, file_id
                )
                if not content:
                    logger.warning(
                        "instagram_pv_adapter_empty_attachment instance=%s file_id=%s",
                        self.instance_key,
                        str(file_id)[:80],
                    )
                    continue
                filename = att.get("filename") or (
                    str(file_path).split("/")[-1] if file_path else "file"
                )
                resolved_ct = content_type
                if resolved_ct and "/" not in str(resolved_ct):
                    resolved_ct = None
                resolved_content_type = BalePvAdapter._normalize_content_type(
                    filename=filename,
                    content_type=resolved_ct or att.get("content_type"),
                    content=content,
                )
                filename = BalePvAdapter._normalize_filename_extension(
                    filename, resolved_content_type
                )
                resolved.append(
                    {
                        "filename": filename,
                        "content": content,
                        "content_type": resolved_content_type,
                    }
                )
            except Exception as exc:
                logger.warning(
                    "instagram_pv_adapter_download_failed instance=%s file_id=%s error=%s",
                    self.instance_key,
                    str(file_id)[:80],
                    exc,
                )
        return resolved

    @staticmethod
    def _extract_attachment_refs(message: Dict[str, Any]) -> List[Dict[str, Any]]:
        refs: List[Dict[str, Any]] = []

        photo = message.get("photo")
        if isinstance(photo, list) and photo:
            candidate = photo[-1]
            if isinstance(candidate, dict) and candidate.get("file_id"):
                refs.append(
                    {
                        "file_id": str(candidate["file_id"]),
                        "filename": "photo.jpg",
                        "content_type": "image/jpeg",
                    }
                )

        for key, filename, default_ct in (
            ("video", "video.mp4", "video/mp4"),
            ("voice", "voice.m4a", "audio/mp4"),
            ("document", "file", None),
        ):
            item = message.get(key)
            if isinstance(item, dict) and item.get("file_id"):
                refs.append(
                    {
                        "file_id": str(item["file_id"]),
                        "filename": item.get("file_name") or filename,
                        "content_type": item.get("mime_type") or default_ct,
                    }
                )

        return refs
