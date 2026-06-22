"""Chatwoot bridge service for adapter-normalized events.

This service replaces the Bale-PV-specific parts of ``BridgeService`` with the
cleaner pattern from evolution-api/messenger_chatwoot_connector:
- one contact/conversation per chat (group id for groups, user id for PV)
- inbound events posted as Chatwoot messages
- Chatwoot webhooks forwarded to the platform adapter
"""

from __future__ import annotations

import logging
import mimetypes
import os.path
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.clients.chatwoot_client import ChatwootClient
from app.connectors.registry import connector_registry
from app.models import (
    BalePvPhoneResolvedUser,
    Conversation,
    Instance,
    MessageDirection,
    MessageKind,
    MessageMapping,
    MessageStatus,
)
from app.runtime_registry import get_runtime
from app.utils.crypto_utils import encryptor

logger = logging.getLogger("app.services.chatwoot_bridge_service")


class ChatwootBridgeService:
    """Bridge normalized platform events to/from Chatwoot."""

    async def ingest_platform_event(
        self,
        db: Session,
        instance_key: str,
        event: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create or update Chatwoot contact/conversation and post an incoming message."""
        logger.info(
            "chatwoot_bridge.ingest_event instance=%s message_id=%s chat_id=%s has_attachments=%s",
            instance_key,
            event.get("message_id"),
            event.get("chat_id"),
            bool(event.get("attachments")),
        )
        instance, chatwoot_cfg, client = self._chatwoot_client_for_instance(db, instance_key)
        if not chatwoot_cfg or chatwoot_cfg.get("enabled") is False:
            return {"ok": False, "detail": "chatwoot_not_enabled"}

        account_id = int(chatwoot_cfg["account_id"])
        inbox_id = int(chatwoot_cfg.get("inbox_id") or 0)
        if not inbox_id:
            return {"ok": False, "detail": "chatwoot_inbox_id_missing"}

        chat_id = str(event["chat_id"])
        chat_type = str(event.get("chat_type") or "private").lower()
        from_name = str(event.get("from_name") or "").strip() or chat_id
        platform_key = "bale_pv_enterprise"
        source_id = connector_registry.prefixed_source_id(
            platform_key,
            str(event.get("platform_message_id") or event.get("message_id") or ""),
        )

        # 1. Get or create contact
        contact_id = await self._get_or_create_contact(
            client,
            account_id=account_id,
            inbox_id=inbox_id,
            chat_id=chat_id,
            from_name=from_name,
            phone_number=(event.get("contact") or {}).get("phone_number"),
            chat_type=chat_type,
            platform_key=platform_key,
        )

        # 2. Get or create conversation
        conversation = await self._get_or_create_conversation(
            db,
            client,
            instance=instance,
            account_id=account_id,
            inbox_id=inbox_id,
            contact_id=contact_id,
            chat_id=chat_id,
        )
        conversation_id = int(conversation.chatwoot_conversation_id) if conversation.chatwoot_conversation_id else 0

        # 3. Detect duplicate inbound messages before posting to Chatwoot.
        # Re-processed platform updates (especially media) must not create a
        # second Chatwoot message just because the first mapping insert failed
        # or the poller retried.
        platform_message_id = str(event.get("platform_message_id") or event.get("message_id") or "")
        if platform_message_id:
            existing = (
                db.query(MessageMapping)
                .filter(
                    MessageMapping.conversation_id == str(conversation.id),
                    MessageMapping.platform_message_id == platform_message_id,
                )
                .first()
            )
            if existing and existing.status == MessageStatus.sent:
                logger.info(
                    "chatwoot_bridge.duplicate_platform_message_skip instance=%s conversation_id=%s platform_message_id=%s chatwoot_message_id=%s",
                    instance_key,
                    conversation.id,
                    platform_message_id,
                    existing.chatwoot_message_id,
                )
                return {
                    "ok": True,
                    "duplicate": True,
                    "chatwoot_conversation_id": conversation_id,
                    "chatwoot_message_id": existing.chatwoot_message_id,
                }

        # 4. Post message
        text = str(event.get("text") or "").strip()
        attachments = event.get("attachments") or []
        is_outgoing = bool(event.get("outgoing"))
        chatwoot_message_type = "outgoing" if is_outgoing else "incoming"

        post_data = {
            "content": text,
            "message_type": chatwoot_message_type,
            "private": False,
            "source_id": source_id,
        }

        try:
            result = await self._post_message_to_chatwoot(
                client, account_id, conversation_id, post_data, attachments
            )
        except httpx.HTTPStatusError as exc:
            if self._is_missing_chatwoot_conversation(exc.response):
                logger.warning(
                    "chatwoot_bridge.conversation_missing instance=%s conversation_id=%s; creating new one",
                    instance_key,
                    conversation_id,
                )
                conversation = await self._recreate_chatwoot_conversation(
                    db,
                    client,
                    instance=instance,
                    account_id=account_id,
                    inbox_id=inbox_id,
                    contact_id=contact_id,
                    chat_id=chat_id,
                    old_conversation=conversation,
                )
                conversation_id = int(conversation.chatwoot_conversation_id) if conversation.chatwoot_conversation_id else 0
                result = await self._post_message_to_chatwoot(
                    client, account_id, conversation_id, post_data, attachments
                )
            else:
                raise

        chatwoot_message_id = self._extract_id(result)

        self._persist_mapping(
            db,
            instance=instance,
            conversation_id=str(conversation.id),
            direction=MessageDirection.platform_to_chatwoot,
            message_kind=MessageKind.text if not attachments else MessageKind.media,
            chatwoot_message_id=str(chatwoot_message_id) if chatwoot_message_id else None,
            platform_message_id=platform_message_id,
        )

        return {
            "ok": True,
            "chatwoot_contact_id": contact_id,
            "chatwoot_conversation_id": conversation_id,
            "chatwoot_message_id": chatwoot_message_id,
        }

    async def handle_chatwoot_webhook(
        self,
        db: Session,
        instance_key: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Forward an outgoing Chatwoot message to the platform adapter."""
        runtime = get_runtime(instance_key)
        if not runtime or runtime.status != "open":
            return {"ok": False, "detail": "instance_not_connected"}

        try:
            instance, chatwoot_cfg, client = self._chatwoot_client_for_instance(
                db, instance_key
            )
        except RuntimeError:
            return {"ok": False, "detail": "instance_not_found"}

        account_id = int(chatwoot_cfg["account_id"])

        event = str(payload.get("event") or "").strip().lower()

        # Mark local conversations inactive when Chatwoot resolves/closes them,
        # so the next inbound message creates a new conversation instead of
        # posting to a stale one.
        if event in ("conversation_status_changed", "status_changed"):
            return self._handle_conversation_status_change(db, instance, payload)

        if event and event != "message_created":
            return {"ok": True, "ignored": True, "reason": f"ignored_event:{event}", "detail": f"ignored_event:{event}"}

        content = str(payload.get("content") or "").strip()
        if payload.get("private") is True:
            return {"ok": True, "ignored": True, "reason": "private_message", "detail": "private_message"}

        # Only forward agent/outgoing replies. Incoming messages are already
        # delivered into Chatwoot by the polling loop; forwarding them back
        # would echo them to the user.
        if str(payload.get("message_type") or "").lower() != "outgoing":
            return {"ok": True, "ignored": True, "reason": "not_outgoing_message", "detail": "not_outgoing_message"}

        source_id = self._extract_source_id(payload)
        if source_id:
            sid = str(source_id).upper()
            for prefix in connector_registry.all_prefixes():
                if sid.startswith(f"{prefix}:"):
                    return {
                        "ok": True,
                        "ignored": True,
                        "reason": "platform_echo",
                        "detail": f"platform_echo:{source_id}",
                    }

        sender = self._extract_chatwoot_sender(payload)
        identifier = sender.get("identifier")
        phone_number = sender.get("phone_number")
        chatwoot_contact_id = sender.get("id")

        peer_id: Optional[str] = None
        if identifier:
            peer_id = self._strip_source_prefix(str(identifier))
        elif phone_number:
            peer_id = str(phone_number).lstrip("+")

        if not peer_id:
            return {"ok": False, "detail": "peer_id_not_found"}

        # For Bale PV, a contact with only a phone number must be resolved to
        # its Bale user id before we can send. The resolved id is also written
        # back to the Chatwoot contact identifier for future messages.
        if (
            runtime.platform_type == "bale_pv_enterprise"
            and not identifier
            and phone_number
            and self._is_phone_number_destination(peer_id)
        ):
            original_peer_id = peer_id
            resolved_user = await self._resolve_bale_pv_phone(
                db, instance, runtime, peer_id, chatwoot_contact_id
            )
            await self._update_chatwoot_contact_for_bale_pv_phone(
                client,
                account_id,
                chatwoot_contact_id,
                resolved_user,
                peer_id,
            )
            peer_id = str(resolved_user["id"])
            # Keep the local conversation mapping in sync with the resolved id.
            await self._sync_conversation_platform_id(
                db, instance, original_peer_id, peer_id
            )

        reply_to = None
        parent_id = payload.get("conversation") and payload["conversation"].get("messages") and payload["conversation"]["messages"][0].get("id")
        if parent_id:
            mapping = (
                db.query(MessageMapping)
                .join(Conversation, MessageMapping.conversation_id == Conversation.id)
                .filter(
                    MessageMapping.chatwoot_message_id == str(parent_id),
                    Conversation.instance_id == instance.id,
                )
                .first()
            )
            if mapping and mapping.platform_message_id:
                reply_to = mapping.platform_message_id

        attachments = self._extract_chatwoot_attachments(payload)

        sent: List[Dict[str, Any]] = []
        if attachments and isinstance(attachments, list):
            for att in attachments:
                data_url = att.get("data_url") if isinstance(att, dict) else None
                if not data_url:
                    continue
                if isinstance(data_url, str) and data_url.startswith("/"):
                    base_url = str(chatwoot_cfg.get("base_url") or "").rstrip("/")
                    data_url = f"{base_url}{data_url}"
                filename = self._attachment_filename(att, data_url)
                result = await runtime.adapter.send_media(
                    peer_id,
                    data_url,
                    filename=filename,
                    caption=content or None,
                    reply_to=reply_to,
                )
                sent.append(result)
        else:
            result = await runtime.adapter.send_text(peer_id, content, reply_to=reply_to)
            sent.append(result)

        return {"ok": True, "peer_id": peer_id, "sent": sent}

    # ------------------------------------------------------------------
    # Chatwoot helpers
    # ------------------------------------------------------------------

    def _chatwoot_client_for_instance(
        self,
        db: Session,
        instance_key: str,
    ) -> tuple[Instance, Dict[str, Any], ChatwootClient]:
        instance = db.query(Instance).filter(Instance.instance_key == instance_key).first()
        if not instance:
            raise RuntimeError(f"Instance '{instance_key}' not found")
        chatwoot_cfg = encryptor.decrypt_json(instance.chatwoot_config_encrypted)
        client = ChatwootClient(
            base_url=str(chatwoot_cfg.get("base_url") or "").rstrip("/"),
            token=str(chatwoot_cfg.get("api_access_token") or chatwoot_cfg.get("token") or ""),
            timeout=30,
        )
        return instance, chatwoot_cfg, client

    async def _get_or_create_contact(
        self,
        client: ChatwootClient,
        *,
        account_id: int,
        inbox_id: int,
        chat_id: str,
        from_name: str,
        phone_number: Optional[str] = None,
        chat_type: str = "private",
        platform_key: str = "bale_pv_enterprise",
    ) -> int:
        """Find or create a Chatwoot contact for this chat."""
        prefixed_identifier = connector_registry.prefixed_source_id(platform_key, chat_id)
        try:
            found = await client.search_contacts(account_id, prefixed_identifier)
            payload = found.get("payload") if isinstance(found, dict) else None
            if isinstance(payload, list) and payload:
                first = payload[0] if isinstance(payload[0], dict) else {}
                cid = self._extract_id(first)
                if cid:
                    return int(cid)
        except Exception:
            pass

        create_payload: Dict[str, Any] = {
            "inbox_id": inbox_id,
            "name": from_name,
            "identifier": prefixed_identifier,
        }
        if phone_number:
            create_payload["phone_number"] = phone_number

        created = await client.create_contact(account_id, create_payload)
        cid = self._extract_id(created) or self._extract_id((created or {}).get("payload"))
        if not cid:
            raise RuntimeError("Failed to create Chatwoot contact")
        return int(cid)

    async def _get_or_create_conversation(
        self,
        db: Session,
        client: ChatwootClient,
        *,
        instance: Instance,
        account_id: int,
        inbox_id: int,
        contact_id: int,
        chat_id: str,
    ) -> Conversation:
        """Find or create a Chatwoot conversation and mirror it locally.

        Avoids reusing resolved/closed remote conversations; a resolved
        conversation must trigger a new one so agent assignment starts fresh.
        """
        existing = (
            db.query(Conversation)
            .filter(
                Conversation.instance_id == instance.id,
                Conversation.platform_conversation_id == chat_id,
                Conversation.is_active.is_(True),
            )
            .first()
        )

        # If we have a local mapping, make sure the remote conversation is still open.
        # If Chatwoot resolved it, mark local inactive and create a new one.
        if existing and existing.chatwoot_conversation_id:
            remote_status = await self._get_remote_conversation_status(
                client, account_id, contact_id, existing.chatwoot_conversation_id, inbox_id
            )
            if remote_status not in ("resolved", "closed"):
                return existing
            existing.is_active = False
            db.add(existing)
            db.commit()

        # Try remote contact conversations in this inbox, skipping resolved/closed.
        try:
            remote_convs = await client.list_contact_conversations(account_id, contact_id)
            for item in remote_convs if isinstance(remote_convs, list) else []:
                if str(item.get("inbox_id")) == str(inbox_id):
                    remote_status = str(item.get("status") or "").strip().lower()
                    if remote_status in ("resolved", "closed"):
                        continue
                    conv_id = self._extract_id(item)
                    if conv_id:
                        return self._ensure_local_conversation(db, instance, chat_id, conv_id, contact_id, inbox_id)
        except Exception:
            pass

        created = await client.create_conversation(
            account_id,
            {"contact_id": str(contact_id), "inbox_id": str(inbox_id)},
        )
        conv_id = self._extract_id(created) or self._extract_id((created or {}).get("payload"))
        if not conv_id:
            raise RuntimeError("Failed to create Chatwoot conversation")
        return self._ensure_local_conversation(db, instance, chat_id, conv_id, contact_id, inbox_id)

    async def _get_remote_conversation_status(
        self,
        client: ChatwootClient,
        account_id: int,
        contact_id: int,
        chatwoot_conversation_id: str,
        inbox_id: int,
    ) -> Optional[str]:
        """Return the remote status of a Chatwoot conversation, or None on error."""
        try:
            remote_convs = await client.list_contact_conversations(account_id, contact_id)
            target = str(chatwoot_conversation_id).strip()
            for item in remote_convs if isinstance(remote_convs, list) else []:
                if (
                    str(item.get("inbox_id")) == str(inbox_id)
                    and str(self._extract_id(item) or "") == target
                ):
                    return str(item.get("status") or "").strip().lower() or None
        except Exception as exc:
            logger.debug(
                "chatwoot_bridge.get_remote_conversation_status_failed account_id=%s conv_id=%s error=%s",
                account_id,
                chatwoot_conversation_id,
                exc,
            )
        return None

    def _ensure_local_conversation(
        self,
        db: Session,
        instance: Instance,
        chat_id: str,
        chatwoot_conversation_id: Any,
        chatwoot_contact_id: Any,
        chatwoot_inbox_id: Any,
    ) -> Conversation:
        existing = (
            db.query(Conversation)
            .filter(
                Conversation.instance_id == instance.id,
                Conversation.platform_conversation_id == chat_id,
            )
            .first()
        )
        if existing:
            existing.is_active = True
            existing.chatwoot_conversation_id = str(chatwoot_conversation_id)
            existing.chatwoot_contact_id = str(chatwoot_contact_id)
            existing.chatwoot_inbox_id = str(chatwoot_inbox_id)
        else:
            existing = Conversation(
                instance_id=instance.id,
                platform_conversation_id=chat_id,
                chatwoot_conversation_id=str(chatwoot_conversation_id),
                chatwoot_contact_id=str(chatwoot_contact_id),
                chatwoot_inbox_id=str(chatwoot_inbox_id),
                is_active=True,
            )
            db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing

    @staticmethod
    def _attachment_filename(att: Dict[str, Any], data_url: str) -> str:
        """Return a filename with a proper extension for a Chatwoot attachment.

        Chatwoot webhooks sometimes omit the extension or only provide a generic
        name. This helper falls back to the URL path or the attachment's
        content-type so Bale can render the file correctly.
        """
        base_name = str(att.get("file_name") or att.get("filename") or "").strip()
        if not base_name:
            base_name = "file"

        # Split an existing extension off the base name so we don't duplicate it.
        name, ext = os.path.splitext(base_name)
        if not ext:
            # Try the URL path first.
            parsed = urlparse(data_url)
            _, url_ext = os.path.splitext(parsed.path)
            if url_ext and "." in url_ext:
                ext = url_ext
            else:
                # Fall back to content-type mapping.
                content_type = str(att.get("content_type") or att.get("file_type") or "").strip()
                if content_type:
                    guessed = mimetypes.guess_extension(content_type)
                    if guessed:
                        ext = guessed
            if not ext:
                ext = ""

        # Avoid names like "file." if we somehow ended up with just a dot.
        ext = ext.strip()
        if ext == ".":
            ext = ""

        return f"{name}{ext}" if ext else name

    @staticmethod
    def _extract_chatwoot_attachments(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return attachment dicts from a Chatwoot webhook payload.

        Chatwoot places attachments either directly on the payload or under
        ``payload["message"]["attachments"]``. The legacy
        ``conversation.messages[0].attachments`` path is also checked for
        backwards compatibility.
        """
        direct = payload.get("attachments")
        if isinstance(direct, list):
            return [item for item in direct if isinstance(item, dict)]

        nested = (payload.get("message") or {}).get("attachments")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]

        conv = payload.get("conversation") or {}
        msgs = conv.get("messages") or []
        if msgs and isinstance(msgs[0], dict):
            legacy = msgs[0].get("attachments")
            if isinstance(legacy, list):
                return [item for item in legacy if isinstance(item, dict)]

        return []

    def _persist_mapping(
        self,
        db: Session,
        *,
        instance: Instance,
        conversation_id: str,
        direction: MessageDirection,
        message_kind: MessageKind,
        chatwoot_message_id: Optional[str],
        platform_message_id: Optional[str],
        chatwoot_parent_message_id: Optional[str] = None,
        platform_parent_message_id: Optional[str] = None,
        status: MessageStatus = MessageStatus.sent,
    ) -> Optional[MessageMapping]:
        """Persist a message mapping, upserting on unique-key conflicts.

        The ``message_mappings`` table has unique constraints on
        ``(conversation_id, platform_message_id)`` and
        ``(conversation_id, chatwoot_message_id)``. Re-processing the same
        platform update (e.g. due to a failed offset commit or a retried poll)
        must not crash with an integrity error; instead we update the existing
        row or skip it if it is already marked as sent.
        """
        existing: Optional[MessageMapping] = None
        if platform_message_id:
            existing = (
                db.query(MessageMapping)
                .filter(
                    MessageMapping.conversation_id == str(conversation_id),
                    MessageMapping.platform_message_id == str(platform_message_id),
                )
                .first()
            )
        if not existing and chatwoot_message_id:
            existing = (
                db.query(MessageMapping)
                .filter(
                    MessageMapping.conversation_id == str(conversation_id),
                    MessageMapping.chatwoot_message_id == str(chatwoot_message_id),
                )
                .first()
            )

        if existing:
            if existing.status == MessageStatus.sent:
                logger.debug(
                    "chatwoot_bridge.mapping_duplicate_skip conversation_id=%s platform_message_id=%s chatwoot_message_id=%s",
                    conversation_id,
                    platform_message_id,
                    chatwoot_message_id,
                )
                return existing
            existing.direction = direction
            existing.message_kind = message_kind
            existing.chatwoot_message_id = chatwoot_message_id
            existing.platform_message_id = platform_message_id
            existing.chatwoot_parent_message_id = chatwoot_parent_message_id
            existing.platform_parent_message_id = platform_parent_message_id
            existing.status = status
            db.add(existing)
            db.commit()
            db.refresh(existing)
            return existing

        mapping = MessageMapping(
            conversation_id=conversation_id,
            direction=direction,
            message_kind=message_kind,
            chatwoot_message_id=chatwoot_message_id,
            platform_message_id=platform_message_id,
            chatwoot_parent_message_id=chatwoot_parent_message_id,
            platform_parent_message_id=platform_parent_message_id,
            status=status,
        )
        db.add(mapping)
        db.commit()
        db.refresh(mapping)
        return mapping

    @staticmethod
    def _extract_peer_id(payload: Dict[str, Any]) -> Optional[str]:
        conv = payload.get("conversation") or {}
        meta = conv.get("meta") or {}
        sender = meta.get("sender") or {}
        identifier = sender.get("identifier")
        if identifier:
            return ChatwootBridgeService._strip_source_prefix(str(identifier))
        phone = sender.get("phone_number")
        if phone:
            return str(phone).lstrip("+")
        return None

    @staticmethod
    def _strip_source_prefix(value: str) -> str:
        raw = str(value or "").strip()
        if ":" not in raw:
            return raw
        prefix, remainder = raw.split(":", 1)
        prefix = str(prefix or "").strip().upper()
        remainder = str(remainder or "").strip()
        if not prefix or not remainder:
            return raw
        if prefix in connector_registry.all_prefixes():
            return remainder
        return raw

    @staticmethod
    def _extract_source_id(payload: Any) -> Optional[str]:
        """Return the Chatwoot source_id from a webhook payload if present."""
        if not isinstance(payload, dict):
            return None
        if payload.get("source_id"):
            return str(payload["source_id"])
        conversation = payload.get("conversation")
        if isinstance(conversation, dict):
            msgs = conversation.get("messages") or []
            if msgs and isinstance(msgs[0], dict) and msgs[0].get("source_id"):
                return str(msgs[0]["source_id"])
        return None

    @staticmethod
    def _extract_id(response: Any) -> Optional[int]:
        if isinstance(response, int):
            return response
        if not isinstance(response, dict):
            return None
        for key in ("id", "contact_id", "conversation_id", "message_id"):
            val = response.get(key)
            if isinstance(val, int):
                return val
        payload = response.get("payload")
        if isinstance(payload, dict):
            for key in ("id", "contact_id", "conversation_id", "message_id"):
                val = payload.get(key)
                if isinstance(val, int):
                    return val
            for nested_key in ("contact", "conversation", "message"):
                nested = payload.get(nested_key)
                if isinstance(nested, dict):
                    for key in ("id", "contact_id", "conversation_id", "message_id"):
                        val = nested.get(key)
                        if isinstance(val, int):
                            return val
        return None

    @staticmethod
    def _extract_chatwoot_sender(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return the sender meta dict from a Chatwoot webhook payload."""
        conv = payload.get("conversation") or {}
        meta = conv.get("meta") or {}
        return meta.get("sender") or {}

    @staticmethod
    def _is_phone_number_destination(value: Optional[str]) -> bool:
        """Return True if the destination looks like a raw phone number."""
        if not value:
            return False
        digits = re.sub(r"\D", "", str(value))
        # Iranian mobile numbers: international 98XXXXXXXXXX or local 0XXXXXXXXXX.
        return bool(re.match(r"^(98\d{10}|0\d{10})$", digits))

    @staticmethod
    def _normalize_bale_pv_phone(phone: str) -> str:
        """Normalize phone number to 98XXXXXXXXXX digits."""
        digits = re.sub(r"\D", "", str(phone or "").strip())
        if digits.startswith("0") and len(digits) == 11:
            digits = "98" + digits[1:]
        return digits

    async def _resolve_bale_pv_phone(
        self,
        db: Session,
        instance: Instance,
        runtime: Any,
        phone_number: str,
        chatwoot_contact_id: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Resolve a phone number to a Bale user, caching the result locally."""
        normalized = self._normalize_bale_pv_phone(phone_number)
        cached = (
            db.query(BalePvPhoneResolvedUser)
            .filter_by(instance_id=instance.id, phone_number=normalized)
            .first()
        )
        if cached:
            access_hash = cached.access_hash
            access_hash_int = int(access_hash) if access_hash and str(access_hash).lstrip("-").isdigit() else None
            runtime.adapter.cache_access_hash(str(cached.bale_user_id), access_hash_int)
            return {
                "id": cached.bale_user_id,
                "access_hash": cached.access_hash,
                "name": cached.name,
                "nick": cached.nick,
            }

        user = await runtime.adapter.resolve_phone_to_user(normalized)
        if not user or not user.get("id"):
            raise RuntimeError(f"Could not resolve Bale user for phone {normalized}")

        access_hash = user.get("access_hash")
        access_hash_str = str(access_hash) if access_hash is not None else None
        cached = BalePvPhoneResolvedUser(
            instance_id=instance.id,
            phone_number=normalized,
            bale_user_id=int(user["id"]),
            access_hash=access_hash_str,
            name=user.get("name"),
            nick=user.get("nick"),
        )
        db.add(cached)
        db.commit()
        return {
            "id": int(user["id"]),
            "access_hash": access_hash_str,
            "name": user.get("name"),
            "nick": user.get("nick"),
        }

    async def _update_chatwoot_contact_for_bale_pv_phone(
        self,
        client: ChatwootClient,
        account_id: int,
        chatwoot_contact_id: Optional[Any],
        resolved_user: Dict[str, Any],
        phone_number: str,
    ) -> None:
        """Update the Chatwoot contact with the resolved Bale identifier and name."""
        if not chatwoot_contact_id:
            return
        try:
            name = (
                resolved_user.get("name")
                or resolved_user.get("nick")
                or f"User {resolved_user['id']}"
            )
            identifier = connector_registry.prefixed_source_id(
                "bale_pv_enterprise", str(resolved_user["id"])
            )
            await client.update_contact(
                account_id,
                int(chatwoot_contact_id),
                {
                    "name": name,
                    "identifier": identifier,
                    "phone_number": self._normalize_bale_pv_phone(phone_number),
                },
            )
        except Exception as exc:
            logger.warning(
                "chatwoot_bridge.update_bale_pv_contact_failed "
                "instance=%s contact_id=%s error=%s",
                getattr(chatwoot_contact_id, "instance_key", None),
                chatwoot_contact_id,
                exc,
            )

    @staticmethod
    async def _sync_conversation_platform_id(
        db: Session,
        instance: Instance,
        original_platform_id: str,
        resolved_platform_id: str,
    ) -> None:
        """Update local conversation mapping when a phone resolves to a user id."""
        if original_platform_id == resolved_platform_id:
            return
        try:
            conversation = (
                db.query(Conversation)
                .filter(
                    Conversation.instance_id == instance.id,
                    Conversation.platform_conversation_id == original_platform_id,
                )
                .first()
            )
            if conversation:
                conversation.platform_conversation_id = resolved_platform_id
                db.commit()
        except Exception as exc:
            logger.warning(
                "chatwoot_bridge.sync_conversation_platform_id_failed "
                "instance=%s original=%s resolved=%s error=%s",
                instance.instance_key,
                original_platform_id,
                resolved_platform_id,
                exc,
            )

    async def _post_message_to_chatwoot(
        self,
        client: ChatwootClient,
        account_id: int,
        conversation_id: int,
        data: Dict[str, Any],
        attachments: List[Dict[str, Any]],
    ) -> Any:
        """Post a message to Chatwoot, with or without attachments."""
        files: List[tuple[str, bytes, Optional[str]]] = []
        if attachments:
            for att in attachments:
                content = att.get("content")
                if not isinstance(content, bytes):
                    continue
                filename = self._unique_attachment_filename(
                    att.get("filename") or "file"
                )
                content_type = att.get("content_type") or "application/octet-stream"
                files.append((filename, content, content_type))

        if files:
            logger.info(
                "chatwoot_bridge.posting_attachments conversation_id=%s count=%s details=%s",
                conversation_id,
                len(files),
                [
                    {"name": f[0], "size": len(f[1]), "type": f[2], "magic": f[1][:8].hex()}
                    for f in files
                ],
            )
            return await client.post_message_with_attachments(
                account_id, conversation_id, data, files
            )
        return await client.post_message(account_id, conversation_id, data)

    @staticmethod
    def _unique_attachment_filename(filename: str) -> str:
        """Return a unique filename so saved GIFs/stickers/media do not collide.

        A short UUID suffix is appended before the extension while preserving
        the original base name. This keeps the file human-readable and avoids
        overwriting earlier attachments that share generic names such as
        ``sticker.webp`` or ``photo.jpg``.
        """
        base = str(filename or "file").strip() or "file"
        path = Path(base)
        return f"{path.stem}_{uuid.uuid4().hex[:8]}{path.suffix}"

    @staticmethod
    def _is_missing_chatwoot_conversation(response: Optional[httpx.Response]) -> bool:
        """Return True if Chatwoot reports the conversation as missing/resolved."""
        if response is None or response.status_code != 404:
            return False
        body = ""
        try:
            body = str(response.text or "").strip().lower()
        except Exception:
            body = ""
        return "resource could not be found" in body or "not found" in body

    async def _recreate_chatwoot_conversation(
        self,
        db: Session,
        client: ChatwootClient,
        *,
        instance: Instance,
        account_id: int,
        inbox_id: int,
        contact_id: int,
        chat_id: str,
        old_conversation: Optional[Conversation] = None,
    ) -> Conversation:
        """Create a new Chatwoot conversation and update the local mapping.

        Marks the old conversation mapping inactive and points the platform chat
        id to the newly created Chatwoot conversation.
        """
        # Try to reuse an existing remote conversation in this inbox first.
        try:
            remote_convs = await client.list_contact_conversations(account_id, contact_id)
            for item in remote_convs if isinstance(remote_convs, list) else []:
                if str(item.get("inbox_id")) == str(inbox_id):
                    remote_status = str(item.get("status") or "").strip().lower()
                    if remote_status not in ("resolved", "closed"):
                        conv_id = self._extract_id(item)
                        if conv_id:
                            return self._ensure_local_conversation(
                                db, instance, chat_id, conv_id, contact_id, inbox_id
                            )
        except Exception as exc:
            logger.warning(
                "chatwoot_bridge.recreate_list_remote_failed instance=%s error=%s",
                instance.instance_key,
                exc,
            )

        # No reusable remote conversation; create a new one.
        created = await client.create_conversation(
            account_id,
            {"contact_id": str(contact_id), "inbox_id": str(inbox_id)},
        )
        new_conv_id = self._extract_id(created) or self._extract_id((created or {}).get("payload"))
        if not new_conv_id:
            raise RuntimeError("Failed to create replacement Chatwoot conversation")

        # Mark the old local mapping inactive so we don't reuse it again.
        if old_conversation:
            old_conversation.is_active = False
            db.add(old_conversation)
            db.commit()

        return self._ensure_local_conversation(
            db, instance, chat_id, new_conv_id, contact_id, inbox_id
        )

    def _handle_conversation_status_change(
        self,
        db: Session,
        instance: Instance,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Mark local conversation inactive when Chatwoot resolves it."""
        try:
            conv_payload = payload.get("conversation") or {}
            status = str(
                payload.get("status") or conv_payload.get("status") or ""
            ).strip().lower()
            chatwoot_conv_id = str(
                self._extract_id(payload)
                or self._extract_id(conv_payload)
                or ""
            )
            if not chatwoot_conv_id or status not in ("resolved", "closed"):
                return {"ok": True, "ignored": True, "reason": "not_resolved"}

            conversation = (
                db.query(Conversation)
                .filter(
                    Conversation.instance_id == instance.id,
                    Conversation.chatwoot_conversation_id == chatwoot_conv_id,
                )
                .first()
            )
            if conversation:
                conversation.is_active = False
                db.commit()
                return {
                    "ok": True,
                    "conversation_id": chatwoot_conv_id,
                    "status": "marked_inactive",
                }
            return {"ok": True, "ignored": True, "reason": "conversation_not_found"}
        except Exception as exc:
            logger.warning(
                "chatwoot_bridge.status_change_failed instance=%s error=%s",
                instance.instance_key,
                exc,
            )
            return {"ok": False, "detail": str(exc)}


chatwoot_bridge = ChatwootBridgeService()
