"""Bale PV adapter for the Chatwoot bridge.

Wraps the existing ``BalePvConnector`` and converts its Bot-API-style updates
into the normalized event shape used by ``ChatwootBridgeService``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from app.adapters.base import BasePlatformAdapter
from app.connectors.bale_pv_connector import bale_pv

logger = logging.getLogger("app.adapters.bale_pv")


class BalePvAdapter(BasePlatformAdapter):
    """Adapter that normalizes Bale PV (userbot) traffic for Chatwoot."""

    def __init__(self, instance_key: str, config: Dict[str, Any]) -> None:
        super().__init__(instance_key, config)
        self._connected = False
        # Cache peer_id -> access_hash so outbound messages to non-contacts work.
        self._access_hash_cache: Dict[str, int] = {}
        self._self_id: Optional[str] = None

    async def connect(self) -> None:
        phone = str(self.config.get("bale_pv_phone_number") or "").strip()
        if not phone:
            raise RuntimeError(f"Bale PV instance '{self.instance_key}' missing phone number")
        await bale_pv.connect(self.instance_key, {"bale_pv_phone_number": phone})
        self._connected = True
        self._self_id = str(bale_pv.get_self_user_id(self.instance_key) or "")
        logger.info("bale_pv_adapter_connected instance=%s self_id=%s", self.instance_key, self._self_id)

    async def disconnect(self) -> None:
        try:
            await bale_pv.disconnect(self.instance_key)
        except Exception as exc:
            logger.debug("bale_pv_adapter_disconnect_error instance=%s error=%s", self.instance_key, exc)
        self._connected = False
        self._access_hash_cache.clear()

    async def resolve_phone_to_user(
        self,
        phone_number: str,
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve a raw phone number to a Bale user via contacts import.

        Caches the returned access_hash so subsequent outbound messages to the
        resolved peer_id work without an explicit access_hash.
        """
        user = await bale_pv.resolve_phone_to_user(
            self.instance_key,
            phone_number,
            name=name,
        )
        access_hash = user.get("access_hash")
        if isinstance(access_hash, (int, str)) and str(access_hash).lstrip("-").isdigit():
            self._access_hash_cache[str(user.get("id") or "")] = int(access_hash)
        return user

    async def send_text(
        self,
        peer_id: str,
        text: str,
        *,
        reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        quoted: Optional[Dict[str, Any]] = None
        if reply_to:
            quoted = {"message_id": reply_to}
        access_hash = self._access_hash_for(peer_id)
        result = await bale_pv.send_text(
            self.instance_key,
            peer_id,
            text,
            quoted=quoted,
            access_hash=access_hash,
        )
        return {"ok": True, "result": result}

    async def send_media(
        self,
        peer_id: str,
        media: Any,
        *,
        filename: Optional[str] = None,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        quoted: Optional[Dict[str, Any]] = None
        if reply_to:
            quoted = {"message_id": reply_to}

        file_bytes: Optional[bytes] = None
        if isinstance(media, bytes):
            file_bytes = media
        elif isinstance(media, str) and media.startswith(("http://", "https://")):
            file_bytes = await self._download_url(media)
        else:
            file_bytes = None

        if not file_bytes:
            raise RuntimeError("No media bytes available to send")

        if not filename:
            filename = "file"

        access_hash = self._access_hash_for(peer_id)
        result = await bale_pv.send_media(
            self.instance_key,
            peer_id,
            file_bytes,
            filename,
            caption=caption or None,
            quoted=quoted,
            access_hash=access_hash,
        )
        return {"ok": True, "result": result}

    async def poll_events(self) -> AsyncIterator[Dict[str, Any]]:
        while self._connected:
            try:
                updates = await bale_pv.get_updates(self.instance_key, timeout=5)
            except Exception as exc:
                logger.warning("bale_pv_adapter_poll_error instance=%s error=%s", self.instance_key, exc)
                await asyncio.sleep(2)
                continue

            if not isinstance(updates, dict) or not updates.get("ok"):
                await asyncio.sleep(0.5)
                continue

            for raw in updates.get("result", []):
                event = self.normalize_incoming_update(raw)
                if not event:
                    continue

                # Eagerly download attachments so the bridge can forward them.
                if event.get("attachments"):
                    try:
                        event["attachments"] = await self.resolve_attachments(event["attachments"])
                    except Exception as exc:
                        logger.warning(
                            "bale_pv_adapter_attachments_failed instance=%s message_id=%s error=%s",
                            self.instance_key,
                            event.get("message_id"),
                            exc,
                        )
                        event["attachments"] = []

                yield event

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

        # Cache access_hash for this peer so we can reply later.
        access_hash = message.get("_sender_access_hash")
        if isinstance(access_hash, int):
            if chat_type == "private":
                self._access_hash_cache[chat_id] = access_hash
            elif str(sender_id) not in ("", "None"):
                self._access_hash_cache[str(sender_id)] = access_hash

        is_outgoing = bool(message.get("_outgoing"))

        # For groups/channels the Chatwoot contact is the group/channel itself.
        if chat_type in ("group", "channel"):
            from_name = str(chat.get("title") or "").strip() or f"Bale {chat_type.title()} {chat_id}"
        elif is_outgoing:
            # Outgoing private message echo: the Chatwoot contact is the recipient,
            # not the sender (us). Prefer the cached contact name from Bale.
            #
            # Safety guard: if chat_id resolves to the authenticated account's own
            # UID (which would happen if is_outgoing was incorrectly set for an
            # incoming message whose peer field points to self), skip the update
            # entirely.  Creating a contact named after the authenticated instance
            # is always wrong; "Saved Messages" echoes should not appear in Chatwoot.
            self_uid = bale_pv.get_self_user_id(self.instance_key)
            if self_uid is not None and chat_id == str(self_uid):
                logger.debug(
                    "bale_pv_adapter_skip_self_echo instance=%s chat_id=%s",
                    self.instance_key,
                    chat_id,
                )
                return None
            recipient_name = bale_pv.get_user_name(self.instance_key, int(chat_id)) if chat_id.isdigit() else None
            from_name = recipient_name or f"Bale User {chat_id}"
        else:
            from_name = sender_name or sender_username or f"Bale User {chat_id}"

        text = str(message.get("text") or message.get("caption") or "").strip()

        # Extract contact card, if present.
        contact = self._extract_contact_payload(message)

        # Extract reply-to reference.
        reply_to = None
        reply_obj = message.get("reply_to_message")
        if isinstance(reply_obj, dict):
            reply_to = {"message_id": str(reply_obj.get("message_id") or "")}

        # Extract attachments as file_id references (downloaded later in poll_events).
        attachments = self._extract_attachment_refs(message)
        if attachments:
            logger.info(
                "bale_pv_adapter_extracted_refs instance=%s message_id=%s refs=%s",
                self.instance_key,
                str(message.get("message_id") or raw_update.get("update_id") or ""),
                [
                    {"filename": ref.get("filename"), "content_type": ref.get("content_type"), "file_id": str(ref.get("file_id", ""))[:80]}
                    for ref in attachments
                ],
            )

        event: Dict[str, Any] = {
            "chat_id": chat_id,
            "chat_type": chat_type,
            "from_name": from_name,
            "text": text,
            "message_id": str(message.get("message_id") or raw_update.get("update_id") or ""),
            "platform_message_id": str(message.get("message_id") or raw_update.get("update_id") or ""),
            "sender_id": str(sender_id) if sender_id is not None else None,
            "sender_username": sender_username or None,
            "attachments": attachments,
            "contact": contact,
            "reply_to": reply_to,
            "outgoing": is_outgoing,
            "edited": bool(message.get("_edited")),
            "raw": raw_update,
        }

        # For groups/channels, also expose the actual sender as a separate contact
        # so Chatwoot agents can open a private 1-on-1 conversation with them.
        if chat_type in ("group", "channel") and sender_id is not None:
            sender_access_hash = None
            if str(sender_id) in self._access_hash_cache:
                sender_access_hash = self._access_hash_cache[str(sender_id)]
            sender_contact_name = sender_name or sender_username or f"Bale User {sender_id}"
            event["sender_contact"] = {
                "identifier": f"BALE_PV:{sender_id}",
                "name": sender_contact_name,
                "phone_number": (contact or {}).get("phone_number"),
                "username": sender_username or None,
                "access_hash": sender_access_hash,
            }

        return event

    def get_self_id(self) -> Optional[str]:
        return self._self_id

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _access_hash_for(self, peer_id: str) -> Optional[int]:
        return self._access_hash_cache.get(peer_id)

    def cache_access_hash(self, peer_id: str, access_hash: Optional[int]) -> None:
        """Cache an access_hash for a peer_id (used after phone resolution)."""
        if isinstance(access_hash, int):
            self._access_hash_cache[peer_id] = access_hash

    async def _download_url(self, url: str) -> bytes:
        import httpx
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

    async def resolve_attachments(self, attachments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        resolved: List[Dict[str, Any]] = []
        for att in attachments:
            file_id = att.get("file_id")
            if not file_id:
                continue
            try:
                content, content_type, file_path = await bale_pv.download_file_by_id(
                    self.instance_key, file_id
                )
                if not content:
                    logger.warning(
                        "bale_pv_adapter_empty_attachment instance=%s file_id=%s filename=%s",
                        self.instance_key,
                        file_id,
                        att.get("filename") or "unknown",
                    )
                    continue
                filename = att.get("filename") or (str(file_path).split("/")[-1] if file_path else "file")
                # Prefer the resolved content-type, but fall back to the attachment
                # hint if the resolver returned something that does not look like
                # a MIME type (e.g. a filename string due to positional mismatch).
                resolved_ct = content_type
                if resolved_ct and "/" not in str(resolved_ct):
                    resolved_ct = None
                resolved_content_type = self._normalize_content_type(
                    filename=filename,
                    content_type=resolved_ct or att.get("content_type"),
                    content=content,
                )
                # Chatwoot cannot display WEBP stickers as-is. Convert them to
                # JPEG (PNG fallback) so they render as normal image attachments.
                if resolved_content_type == "image/webp":
                    original_size = len(content)
                    converted, ext, converted_ct = self._convert_webp(content)
                    if converted and ext and converted_ct:
                        content = converted
                        filename = str(filename).rsplit(".", 1)[0] + ext
                        resolved_content_type = converted_ct
                        logger.info(
                            "bale_pv_adapter_sticker_converted instance=%s original_size=%s converted_size=%s format=%s",
                            self.instance_key,
                            original_size,
                            len(converted),
                            ext,
                        )
                    else:
                        logger.warning(
                            "bale_pv_adapter_sticker_conversion_failed instance=%s file_id=%s size=%s",
                            self.instance_key,
                            file_id,
                            original_size,
                        )

                # Make sure the filename extension matches the actual content type.
                # Bale sometimes sends JPEG stickers named *.png, which breaks
                # Chatwoot's image processing.
                original_filename = filename
                filename = self._normalize_filename_extension(
                    filename, resolved_content_type
                )

                logger.info(
                    "bale_pv_adapter_attachment_ready instance=%s file_id=%s original_name=%s final_name=%s content_type=%s size=%s magic=%s",
                    self.instance_key,
                    file_id,
                    original_filename,
                    filename,
                    resolved_content_type,
                    len(content),
                    content[:8].hex() if content else "empty",
                )

                resolved.append(
                    {
                        "filename": filename,
                        "content": content,
                        "content_type": resolved_content_type,
                    }
                )
            except Exception as exc:
                logger.warning("bale_pv_adapter_download_failed instance=%s file_id=%s error=%s", self.instance_key, file_id, exc)
        return resolved

    @staticmethod
    def _extract_attachment_refs(message: Dict[str, Any]) -> List[Dict[str, Any]]:
        refs: List[Dict[str, Any]] = []

        photo = message.get("photo")
        if isinstance(photo, list) and photo:
            candidate = photo[-1]
            if isinstance(candidate, dict) and candidate.get("file_id"):
                refs.append({"file_id": str(candidate["file_id"]), "filename": "photo.jpg", "content_type": "image/jpeg"})

        for key, filename, default_ct in (
            ("video", "video.mp4", "video/mp4"),
            ("audio", "audio.ogg", None),
            ("voice", "voice.ogg", "audio/ogg"),
            ("document", "file", None),
            ("sticker", "sticker.webp", "image/webp"),
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

    @staticmethod
    def _extract_contact_payload(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        contact = message.get("contact")
        if not isinstance(contact, dict):
            return None
        phone = str(contact.get("phone_number") or "").strip()
        if not phone:
            return None
        return {
            "phone_number": phone,
            "first_name": str(contact.get("first_name") or "").strip() or None,
            "last_name": str(contact.get("last_name") or "").strip() or None,
            "user_id": str(contact.get("user_id") or "").strip() or None,
        }

    # Canonical extension for common content types. We prefer a hard-coded map
    # because ``mimetypes.guess_extension`` can return platform-specific aliases
    # (e.g. ``.jpe`` for JPEG on some systems) that confuse Chatwoot.
    _CANONICAL_EXTENSIONS: Dict[str, str] = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
        "audio/ogg": ".ogg",
        "video/mp4": ".mp4",
    }

    @classmethod
    def _normalize_filename_extension(
        cls,
        filename: str,
        content_type: Optional[str],
    ) -> str:
        """Return a filename whose extension matches the actual content type.

        Fixes cases like Bale stickers that are JPEG bytes but named *.png.
        """
        import mimetypes

        if not content_type or "/" not in content_type:
            return filename

        base = str(filename or "file").strip()
        expected_ext = cls._CANONICAL_EXTENSIONS.get(content_type.lower())
        if not expected_ext:
            expected_ext = mimetypes.guess_extension(content_type, strict=False)
        if not expected_ext:
            return base

        expected = expected_ext.lstrip(".").lower()

        if "." not in base:
            return f"{base}.{expected}"

        name_part, current_ext = base.rsplit(".", 1)
        current = current_ext.lower()

        # Accept common aliases so we do not rewrite *.jpg to *.jpg.
        aliases: Dict[str, List[str]] = {
            "jpg": ["jpg", "jpeg"],
            "jpeg": ["jpg", "jpeg"],
            "png": ["png"],
            "gif": ["gif"],
            "webp": ["webp"],
        }
        allowed = aliases.get(expected, [expected])
        if current not in allowed:
            return f"{name_part}.{expected}"
        return base

    @staticmethod
    def _detect_content_type_from_bytes(content: bytes) -> Optional[str]:
        """Detect MIME type from magic bytes."""
        if content.startswith(b"%PDF"):
            return "application/pdf"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if len(content) > 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return "image/webp"
        if content.startswith(b"OggS"):
            return "audio/ogg"
        if len(content) > 8 and content[4:8] == b"ftyp":
            return "video/mp4"
        return None

    @classmethod
    def _normalize_content_type(
        cls,
        *,
        filename: str,
        content_type: Optional[str],
        content: bytes,
    ) -> Optional[str]:
        """Return the most reliable content type for ``content``.

        Magic bytes are the source of truth. Declared content types and filename
        extensions are only used as hints, because Bale sometimes sends JPEG
        bytes with a ``*.png`` name and a ``image/jpeg`` MIME type (and vice
        versa). Detecting from the actual bytes avoids both the extension and
        the declared type lying to Chatwoot.
        """
        detected = cls._detect_content_type_from_bytes(content)
        raw = str(content_type or "").strip().lower().split(";")[0]
        guessed = mimetypes.guess_type(str(filename or "").strip())[0]
        if guessed:
            guessed = guessed.lower().split(";")[0]

        if detected:
            if raw and raw != "application/octet-stream" and raw != detected:
                logger.info(
                    "bale_pv_adapter_ct_mismatch filename=%s declared=%s detected=%s",
                    filename,
                    raw,
                    detected,
                )
            return detected

        if raw and raw != "application/octet-stream":
            return raw
        if guessed:
            return guessed
        return None

    @staticmethod
    def _convert_webp_to_jpeg(webp_bytes: bytes) -> Optional[bytes]:
        try:
            from PIL import Image
            from io import BytesIO
            img = Image.open(BytesIO(webp_bytes))
            # JPEG does not support alpha. Composite onto a white background so
            # transparent stickers don't turn black.
            if img.mode in ("RGBA", "P", "LA"):
                if img.mode == "P":
                    img = img.convert("RGBA")
                background = Image.new("RGBA", img.size, (255, 255, 255, 255))
                background.paste(img, mask=img.split()[-1])
                img = background.convert("RGB")
            else:
                img = img.convert("RGB")
            out = BytesIO()
            img.save(out, format="JPEG", quality=85)
            return out.getvalue()
        except Exception as exc:
            logger.warning("bale_pv_webp_to_jpeg_failed error=%s", exc)
            return None

    @staticmethod
    def _convert_webp_to_png(webp_bytes: bytes) -> Optional[bytes]:
        try:
            from PIL import Image
            from io import BytesIO
            img = Image.open(BytesIO(webp_bytes))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
            out = BytesIO()
            img.save(out, format="PNG")
            return out.getvalue()
        except Exception as exc:
            logger.warning("bale_pv_webp_to_png_failed error=%s", exc)
            return None

    @classmethod
    def _convert_webp(cls, webp_bytes: bytes) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
        """Convert WEBP to JPEG (preferred) or PNG fallback. Returns (bytes, ext, mime)."""
        jpeg = cls._convert_webp_to_jpeg(webp_bytes)
        if jpeg:
            return jpeg, ".jpg", "image/jpeg"
        png = cls._convert_webp_to_png(webp_bytes)
        if png:
            return png, ".png", "image/png"
        return None, None, None
