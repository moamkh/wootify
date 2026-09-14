"""Normalize Eitaa TL messages for Wootify's generic Chatwoot bridge."""

from __future__ import annotations

import asyncio
import mimetypes
from collections import OrderedDict
from typing import Any, AsyncIterator, Optional

from wootify.adapters.base import BasePlatformAdapter
from wootify.config import settings
from wootify.plugins.eitaa_pv.connector import eitaa_pv


class EitaaPvAdapter(BasePlatformAdapter):
    """Adapter whose external contract mirrors the Bale PV personal adapter."""

    async def connect(self) -> None:
        await eitaa_pv.connect(self.instance_key, self.config)

    async def disconnect(self) -> None:
        await eitaa_pv.disconnect(self.instance_key)

    async def send_text(self, peer_id: str, text: str, *, reply_to: Optional[str] = None, mirror_echo: bool = True) -> dict[str, Any]:
        result = await eitaa_pv.send_text(self.instance_key, peer_id, text, {"message_id": reply_to} if reply_to else None)
        return self._sent_result(peer_id, result, mirror_echo=mirror_echo)

    async def send_media(self, peer_id: str, media: Any, *, filename: Optional[str] = None, caption: Optional[str] = None, reply_to: Optional[str] = None, mirror_echo: bool = True) -> dict[str, Any]:
        result = await eitaa_pv.send_media(self.instance_key, peer_id, media, filename or "file", caption, {"message_id": reply_to} if reply_to else None)
        return self._sent_result(peer_id, result, mirror_echo=mirror_echo)

    def _sent_result(self, peer_id: str, response: dict[str, Any], *, mirror_echo: bool) -> dict[str, Any]:
        result = dict(response)
        message_id = result.get("message_id")
        if result.get("_") == "updateShortSentMessage":
            message_id = result.get("id")
        updates = result.get("updates") or []
        for update in updates:
            if update.get("_") == "updateMessageID":
                message_id = update.get("id")
                break
            if update.get("_") in {"updateNewMessage", "updateNewChannelMessage"}:
                message_id = (update.get("message") or {}).get("id")
        if message_id is not None:
            result["message_id"] = str(message_id)
            if not mirror_echo:
                # Cover the interval between the send ACK and DB persistence.
                # The persisted mapping handles echoes after a restart.
                if not hasattr(self, "_sent_echoes"):
                    self._sent_echoes = OrderedDict()
                self._sent_echoes[(str(peer_id), str(message_id))] = None
                while len(self._sent_echoes) > 2048:
                    self._sent_echoes.popitem(last=False)
        return {"ok": True, "result": result}

    async def edit_message(self, peer_id: str, message_id: str, text: str) -> dict[str, Any]:
        return {"ok": True, "result": await eitaa_pv.update_message(self.instance_key, peer_id, message_id, text)}

    async def delete_message(self, peer_id: str, message_id: str) -> dict[str, Any]:
        return {"ok": True, "result": await eitaa_pv.delete_message(self.instance_key, peer_id, message_id)}

    async def prepare_outbound(self, peer_id: str) -> None:
        await eitaa_pv.prepare_outbound(self.instance_key, peer_id)

    async def finish_outbound(self, peer_id: str) -> None:
        await eitaa_pv.finish_outbound(self.instance_key, peer_id)

    async def poll_events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            result = await eitaa_pv.get_updates(self.instance_key)
            for raw in result.get("result") or []:
                event = self.normalize_incoming_update(raw)
                if event:
                    yield event
            # Keep the standalone adapter path consistent with the shared
            # polling service: Eitaa inherits Bale's Wootify default.
            await asyncio.sleep(max(1, int(self.config.get("eitaa_pv_poll_interval") or settings.BALE_POLL_INTERVAL_SECONDS)))

    def normalize_incoming_update(self, raw_update: Any) -> Optional[dict[str, Any]]:
        if not isinstance(raw_update, dict) or not isinstance(raw_update.get("message"), dict):
            return None
        message = raw_update["message"]
        chat_id = str(raw_update.get("chat_id") or "")
        if not chat_id:
            return None
        if (
            bool(message.get("out") or (message.get("pFlags") or {}).get("out"))
            and not message.get("edit_date")
            and (chat_id, str(message.get("id"))) in getattr(self, "_sent_echoes", {})
        ):
            return None
        entities = raw_update.get("entities") or {}
        chat_type = "channel" if chat_id.startswith("channel:") else "group" if chat_id.startswith("chat:") else "private"
        sender_id = self._sender_id(message)
        sender = entities.get(("user", sender_id), {}) if sender_id is not None else {}
        # In a one-to-one dialog, ``from_id`` is the authenticated account for
        # outgoing messages.  The dialog peer is always the customer, so it is
        # the only correct source for the Chatwoot contact name/identifier.
        if chat_type == "private":
            peer = raw_update.get("peer") or {}
            peer_id = peer.get("user_id") if isinstance(peer, dict) and peer.get("_") == "peerUser" else None
            if peer_id is not None:
                try:
                    peer_id = int(peer_id)
                except (TypeError, ValueError):
                    peer_id = None
            peer_entity = entities.get(("user", peer_id), {}) if peer_id is not None else {}
            if peer_entity:
                sender = peer_entity
        name = " ".join(str(sender.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
        if not name:
            name = str(sender.get("username") or f"Eitaa User {sender_id or chat_id}")
        attachments = self._attachment_refs(chat_id, message)
        return {
            "chat_id": chat_id,
            "chat_type": chat_type,
            "from_name": name,
            "text": str(message.get("message") or "").strip(),
            "message_id": str(message.get("id") or ""),
            "platform_message_id": str(message.get("id") or ""),
            "sender_id": str(sender_id) if sender_id is not None else None,
            "sender_username": str(sender.get("username") or "") or None,
            "attachments": attachments,
            "reply_to": {"message_id": str(message.get("reply_to_msg_id"))} if message.get("reply_to_msg_id") else None,
            "outgoing": bool(message.get("out") or (message.get("pFlags") or {}).get("out")),
            "edited": bool(message.get("edit_date")),
            "raw": raw_update,
        }

    def get_self_id(self) -> Optional[str]:
        return eitaa_pv.get_self_id(self.instance_key)

    async def resolve_attachments(self, attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Resolve Eitaa message-media references to Chatwoot upload payloads."""
        resolved: list[dict[str, Any]] = []
        for attachment in attachments:
            chat_id = str(attachment.get("chat_id") or "")
            message_id = str(attachment.get("message_id") or "")
            if not chat_id or not message_id:
                continue
            try:
                content, filename, content_type = await eitaa_pv.download_message_media(
                    self.instance_key,
                    chat_id,
                    message_id,
                    str(attachment.get("filename") or "attachment"),
                    str(attachment.get("content_type") or "") or None,
                )
                resolved.append({
                    "filename": filename,
                    "content": content,
                    "content_type": content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
                })
            except Exception:
                # The poller turns an all-failed attachment batch into a durable
                # retry instead of creating a hollow Chatwoot message.
                continue
        return resolved

    @staticmethod
    def _sender_id(message: dict[str, Any]) -> Optional[int]:
        sender = message.get("from_id") or {}
        value = sender.get("user_id") if isinstance(sender, dict) else None
        return int(value) if value is not None else None

    @staticmethod
    def _attachment_refs(chat_id: str, message: dict[str, Any]) -> list[dict[str, Any]]:
        media = message.get("media")
        if not isinstance(media, dict) or not media:
            return []
        media_kind = str(media.get("_") or "media").lower()
        if media_kind in {"messagemediaempty", "messagemediawebpage"}:
            return []
        document = media.get("document") if isinstance(media.get("document"), dict) else {}
        attributes = document.get("attributes") if isinstance(document.get("attributes"), list) else []
        content_type = str(document.get("mime_type") or "").strip().lower()
        filename = "attachment"
        for attribute in attributes:
            if isinstance(attribute, dict) and attribute.get("file_name"):
                filename = str(attribute["file_name"])
                break
        if "photo" in media_kind:
            filename = "photo.jpg"
            content_type = "image/jpeg"
        elif content_type.startswith("video/") and "." not in filename:
            filename = "video" + (mimetypes.guess_extension(content_type) or ".mp4")
        elif content_type.startswith("audio/") and "." not in filename:
            filename = "audio" + (mimetypes.guess_extension(content_type) or ".ogg")
        return [{
            "file_id": f"{chat_id}:{message.get('id')}",
            "chat_id": chat_id,
            "message_id": str(message.get("id") or ""),
            "filename": filename,
            "content_type": content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        }]
