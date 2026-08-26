"""Pure parsers for the loosely-shaped Chatwoot/platform payloads.

Webhook payloads have changed shape across Chatwoot versions.  Keeping the
shape compatibility rules in small stateless objects makes the bridge
orchestrators easier to reason about without changing their public API.
"""
from __future__ import annotations

import re
from typing import Any, Optional


class MessagePayloadParser:
    """Parse common Chatwoot message/contact fields."""

    @staticmethod
    def extract_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
        direct = payload.get("attachments")
        if isinstance(direct, list):
            return [item for item in direct if isinstance(item, dict)]
        nested = (payload.get("message") or {}).get("attachments")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        return []

    @staticmethod
    def extract_message_text(payload: dict[str, Any]) -> str:
        message_obj = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        for value in (
            payload.get("content"),
            message_obj.get("content"),
            payload.get("processed_message_content"),
            message_obj.get("processed_message_content"),
        ):
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def normalize_message_type(value: Any) -> str:
        if isinstance(value, int):
            return {0: "incoming", 1: "outgoing", 2: "activity", 3: "template"}.get(value, str(value))
        return str(value or "").strip().lower()

    @classmethod
    def is_forwardable_message(cls, payload: dict[str, Any], event_name: str) -> bool:
        message_obj = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        message_type = cls.normalize_message_type(payload.get("message_type"))
        nested_type = cls.normalize_message_type(message_obj.get("message_type"))
        event = str(event_name or "").strip().lower()
        if message_type == "outgoing" or nested_type == "outgoing":
            return True
        return event == "message_created" and (message_type == "template" or nested_type == "template")

    @staticmethod
    def is_message_deleted(payload: dict[str, Any]) -> bool:
        content_attributes = payload.get("content_attributes") if isinstance(payload.get("content_attributes"), dict) else {}
        message_obj = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        msg_content_attributes = message_obj.get("content_attributes") if isinstance(message_obj.get("content_attributes"), dict) else {}
        return bool(content_attributes.get("deleted") or msg_content_attributes.get("deleted"))

    @staticmethod
    def extract_conversation_id(payload: dict[str, Any]) -> Optional[str]:
        conversation = payload.get("conversation") if isinstance(payload.get("conversation"), dict) else {}
        cid = conversation.get("id") or payload.get("conversation_id") or payload.get("conversationId")
        return str(cid) if cid is not None and str(cid).strip() else None

    @staticmethod
    def extract_message_id(payload: dict[str, Any]) -> Optional[str]:
        candidate = payload.get("id")
        if candidate is None:
            message_obj = payload.get("message") if isinstance(payload.get("message"), dict) else {}
            candidate = message_obj.get("id")
        return str(candidate) if candidate is not None and str(candidate).strip() else None

    @staticmethod
    def extract_parent_message_id(payload: dict[str, Any]) -> Optional[str]:
        content_attributes = payload.get("content_attributes") if isinstance(payload.get("content_attributes"), dict) else {}
        message_obj = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        msg_content_attributes = message_obj.get("content_attributes") if isinstance(message_obj.get("content_attributes"), dict) else {}
        candidates = [
            content_attributes.get("in_reply_to"), content_attributes.get("in_reply_to_message_id"),
            msg_content_attributes.get("in_reply_to"), msg_content_attributes.get("in_reply_to_message_id"),
            payload.get("in_reply_to"), message_obj.get("in_reply_to"),
            payload.get("reply_to_message_id"), message_obj.get("reply_to_message_id"),
        ]
        for value in candidates:
            if value is not None and str(value).strip():
                return str(value).strip()
        return None

    @staticmethod
    def extract_contact_id(payload: dict[str, Any]) -> Optional[str]:
        conversation = payload.get("conversation") if isinstance(payload.get("conversation"), dict) else {}
        meta = conversation.get("meta") if isinstance(conversation.get("meta"), dict) else {}
        sender = meta.get("sender") if isinstance(meta.get("sender"), dict) else {}
        value = sender.get("id")
        return str(value).strip() if value is not None and str(value).strip() else None

    @staticmethod
    def normalize_phone_number(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None
        compact = re.sub(r"\s+", "", text)
        if compact.startswith("+"):
            digits = re.sub(r"\D", "", compact[1:])
            return f"+{digits}" if digits else None
        if compact.startswith("00"):
            digits = re.sub(r"\D", "", compact[2:])
            return f"+{digits}" if digits else None
        digits = re.sub(r"\D", "", compact)
        if not digits:
            return None
        if 8 <= len(digits) <= 15 and not digits.startswith("0"):
            return f"+{digits}"
        return digits

    @classmethod
    def extract_phone_from_shared_text(cls, value: Any) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None
        labeled = re.search(r"(?i)shared\s+phone\s+number\s*:\s*([+\d][\d\-\s().]{5,})", text)
        candidate = labeled.group(1) if labeled else None
        if not candidate:
            generic = re.search(r"(?<!\d)(?:\+|00)?\d[\d\-\s().]{6,}\d(?!\d)", text)
            candidate = generic.group(0) if generic else None
        return cls.normalize_phone_number(candidate) if candidate else None

    @staticmethod
    def extract_contact_payload(value: Any) -> dict[str, Any]:
        payload = value if isinstance(value, dict) else {}
        if isinstance(payload.get("payload"), dict):
            payload = payload.get("payload") or {}
        if isinstance(payload.get("contact"), dict):
            payload = payload.get("contact") or {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def build_contact_update_payload(current_contact: dict[str, Any], normalized_phone: str, *, fallback_name: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": str(current_contact.get("name") or "").strip() or fallback_name,
            "phone_number": normalized_phone,
        }
        for key in ("identifier", "email"):
            value = str(current_contact.get(key) or "").strip()
            if value:
                payload[key] = value
        return payload

    @staticmethod
    def to_plus_phone_candidate(phone: Optional[str]) -> Optional[str]:
        text = str(phone or "").strip()
        if not text or text.startswith("+"):
            return None
        digits = re.sub(r"\D", "", text)
        if not digits or digits.startswith("0") or len(digits) < 8 or len(digits) > 15:
            return None
        return f"+{digits}"


class ChatwootPayloadParser:
    """Parse fields specific to the Chatwoot-to-platform direction."""

    normalize_message_type = staticmethod(MessagePayloadParser.normalize_message_type)

    @staticmethod
    def extract_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
        direct = payload.get("attachments")
        if isinstance(direct, list):
            return [item for item in direct if isinstance(item, dict)]
        nested = (payload.get("message") or {}).get("attachments")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        conversation = payload.get("conversation") or {}
        messages = conversation.get("messages") or []
        if messages and isinstance(messages[0], dict):
            legacy = messages[0].get("attachments")
            if isinstance(legacy, list):
                return [item for item in legacy if isinstance(item, dict)]
        return []

    @staticmethod
    def strip_source_prefix(value: str, known_prefixes: set[str]) -> str:
        raw = str(value or "").strip()
        if ":" not in raw:
            return raw
        prefix, remainder = raw.split(":", 1)
        prefix, remainder = str(prefix or "").strip().upper(), str(remainder or "").strip()
        if prefix and remainder and prefix in known_prefixes:
            return remainder
        return raw

    @classmethod
    def extract_peer_id(cls, payload: dict[str, Any], known_prefixes: set[str]) -> Optional[str]:
        conv = payload.get("conversation") or {}
        meta, sender = conv.get("meta") or {}, (conv.get("meta") or {}).get("sender") or {}
        identifier = sender.get("identifier")
        if identifier:
            return cls.strip_source_prefix(str(identifier), known_prefixes)
        phone = sender.get("phone_number")
        return str(phone).lstrip("+") if phone else None

    @staticmethod
    def extract_source_id(payload: Any) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        if payload.get("source_id"):
            return str(payload["source_id"])
        conversation = payload.get("conversation")
        messages = conversation.get("messages") or [] if isinstance(conversation, dict) else []
        if messages and isinstance(messages[0], dict) and messages[0].get("source_id"):
            return str(messages[0]["source_id"])
        return None

    @staticmethod
    def extract_sender(payload: dict[str, Any]) -> dict[str, Any]:
        conv = payload.get("conversation") or {}
        meta = conv.get("meta") or {}
        return meta.get("sender") or {}

    @staticmethod
    def is_bot_sender(payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        candidates = [payload.get("sender"), (payload.get("message") or {}).get("sender")]
        bot_types = ("agent_bot", "bot", "automation")
        for candidate in candidates:
            if isinstance(candidate, dict) and str(candidate.get("type") or candidate.get("sender_type") or "").strip().lower() in bot_types:
                return True
        return str(payload.get("sender_type") or "").strip().lower() in bot_types

    @staticmethod
    def is_generic_contact_name(value: Optional[str]) -> bool:
        if not value:
            return True
        name = str(value).strip()
        return any(name.startswith(prefix) for prefix in ("Group ", "Channel ", "Bale Group ", "Bale Channel ", "Bale User ", "User "))

    @staticmethod
    def is_phone_number_destination(value: Optional[str]) -> bool:
        if not value:
            return False
        digits = re.sub(r"\D", "", str(value))
        return bool(re.match(r"^(98\d{10}|0098\d{10}|0\d{10}|9\d{9})$", digits))

    @classmethod
    def is_foreign_contact_identifier(cls, value: Optional[Any], known_prefixes: set[str]) -> bool:
        raw = str(value or "").strip()
        if not raw: return False
        if cls.strip_source_prefix(raw, known_prefixes) != raw: return False
        if "@" in raw: return True
        return any(ch.isalpha() for ch in raw)

    @staticmethod
    def normalize_bale_pv_phone(phone: str) -> str:
        digits = re.sub(r"\D", "", str(phone or "").strip())
        if digits.startswith("00"):
            digits = digits[2:]
        elif digits.startswith("0") and len(digits) == 11:
            digits = "98" + digits[1:]
        if len(digits) == 10 and digits.startswith("9"):
            digits = "98" + digits
        return digits

    @staticmethod
    def extract_id(response: Any) -> Optional[int]:
        if isinstance(response, int):
            return response
        if not isinstance(response, dict):
            return None
        keys = ("id", "contact_id", "conversation_id", "message_id")
        for key in keys:
            if isinstance(response.get(key), int):
                return response[key]
        payload = response.get("payload")
        if isinstance(payload, dict):
            for key in keys:
                if isinstance(payload.get(key), int):
                    return payload[key]
            for nested_key in ("contact", "conversation", "message"):
                nested = payload.get(nested_key)
                if isinstance(nested, dict):
                    for key in keys:
                        if isinstance(nested.get(key), int):
                            return nested[key]
        return None
