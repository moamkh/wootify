"""Normalize Eitaa TL messages for Wootify's generic Chatwoot bridge."""

from __future__ import annotations

import asyncio
import mimetypes
import math
from collections import OrderedDict
from typing import Any, AsyncIterator, Optional

from wootify.adapters.base import BasePlatformAdapter
from wootify.plugins.eitaa_pv.connector import eitaa_pv


class EitaaPvAdapter(BasePlatformAdapter):
    """Adapter whose external contract mirrors the Bale PV personal adapter."""

    async def connect(self) -> None:
        await eitaa_pv.connect(self.instance_key, self.config)

    async def disconnect(self) -> None:
        await eitaa_pv.disconnect(self.instance_key)

    async def send_text(self, peer_id: str, text: str, *, reply_to: Optional[str] = None, mirror_echo: bool = True, idempotency_key: Optional[str] = None) -> dict[str, Any]:
        result = await eitaa_pv.send_text(self.instance_key, peer_id, text, {"message_id": reply_to} if reply_to else None, idempotency_key=idempotency_key)
        return self._sent_result(peer_id, result, mirror_echo=mirror_echo)

    async def send_media(self, peer_id: str, media: Any, *, filename: Optional[str] = None, caption: Optional[str] = None, reply_to: Optional[str] = None, mirror_echo: bool = True, idempotency_key: Optional[str] = None) -> dict[str, Any]:
        result = await eitaa_pv.send_media(self.instance_key, peer_id, media, filename or "file", caption, {"message_id": reply_to} if reply_to else None, idempotency_key=idempotency_key)
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
        if message_id is None:
            raise RuntimeError(f"eitaa_send_not_acknowledged: {result.get('_', 'unknown response')}")
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

    async def resolve_phone_to_user(self, phone_number: str) -> dict[str, Any]:
        return await eitaa_pv.resolve_phone_to_user(self.instance_key, phone_number)

    async def finish_outbound(self, peer_id: str) -> None:
        await eitaa_pv.finish_outbound(self.instance_key, peer_id)

    async def poll_events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            result = await eitaa_pv.get_updates(self.instance_key)
            for raw in result.get("result") or []:
                event = self.normalize_incoming_update(raw)
                if event:
                    yield event
            eitaa_pv.acknowledge_updates(self.instance_key)
            await asyncio.sleep(eitaa_pv.poll_interval(self.instance_key))

    def normalize_incoming_update(self, raw_update: Any) -> Optional[dict[str, Any]]:
        if isinstance(raw_update, dict) and raw_update.get("channel_gap"):
            raise RuntimeError(f"eitaa_channel_difference_required: {raw_update.get('chat_id')}")
        if isinstance(raw_update, dict) and raw_update.get("deleted"):
            return {"chat_id": str(raw_update.get("chat_id") or ""),
                    "message_id": str(raw_update.get("message_id") or ""), "deleted": True,
                    "eitaa_global_delete": not bool(raw_update.get("chat_id")), "raw": raw_update}
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
        if isinstance(entities, list):
            entities = {(item["kind"], int(item["id"])): item["entity"] for item in entities}
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
            sender = peer_entity  # Never substitute the logged-in account for a missing customer.
        name = " ".join(str(sender.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
        if not name:
            name = str(sender.get("username") or f"Eitaa User {chat_id}")
        if chat_type in {"group", "channel"}:
            kind = "chat" if chat_type == "group" else "channel"
            group_entity = entities.get((kind, int(chat_id.split(":")[-1])), {})
            name = str(group_entity.get("title") or f"Eitaa {chat_type.title()} {chat_id.split(':')[-1]}")
        attachments = self._attachment_refs(chat_id, message)
        return {
            "chat_id": chat_id,
            "chat_type": chat_type,
            "from_name": name,
            "text": self._message_text(message),
            "message_id": str(message.get("id") or ""),
            "platform_message_id": str(message.get("id") or ""),
            "sender_id": str(sender_id) if sender_id is not None else None,
            "sender_username": str(sender.get("username") or "") or None,
            "contact": {"phone_number": str(peer_entity["phone"])} if chat_type == "private" and peer_entity.get("phone") else {},
            "attachments": attachments,
            "reply_to": self._reply_reference(message),
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
    def _reply_reference(message: dict[str, Any]) -> Optional[dict[str, str]]:
        header = message.get("reply_to") if isinstance(message.get("reply_to"), dict) else {}
        mid = header.get("reply_to_msg_id") or message.get("reply_to_msg_id")
        return {"message_id": str(mid)} if mid else None

    @staticmethod
    def _message_text(message: dict[str, Any]) -> str:
        text = str(message.get("message") or "").strip()
        media = message.get("media") or {}
        kind = media.get("_") if isinstance(media, dict) else None
        if kind in {"messageMediaGeo", "messageMediaGeoLive", "messageMediaVenue"}:
            geo = media.get("geo") or {}
            try:
                lat, lon = float(geo["lat"]), float(geo["long"])
                if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError("invalid coordinates")
            except (KeyError, TypeError, ValueError):
                return text or "Location unavailable (invalid coordinates)"
            label = "\n".join(str(media.get(key) or "").strip() for key in ("title", "address")).strip()
            location = f"Location: https://www.google.com/maps/search/?api=1&query={lat},{lon}"
            return "\n".join(value for value in (text, label, location) if value)
        if kind == "messageMediaContact":
            name = " ".join(str(media.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
            return "\n".join(value for value in (text, f"Contact: {name}", str(media.get("phone_number") or "")) if value)
        return text

    @staticmethod
    def _sender_id(message: dict[str, Any]) -> Optional[int]:
        sender = message.get("from_id") or {}
        value = sender.get("user_id") if isinstance(sender, dict) else sender
        return int(value) if value is not None else None

    @staticmethod
    def _attachment_refs(chat_id: str, message: dict[str, Any]) -> list[dict[str, Any]]:
        media = message.get("media")
        if not isinstance(media, dict) or not media:
            return []
        media_kind = str(media.get("_") or "media").lower()
        if media_kind not in {"messagemediaphoto", "messagemediadocument"}:
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
