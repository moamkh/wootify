"""Destination selection policies for Chatwoot-originated messages."""
from __future__ import annotations

import re
from typing import Any, Optional


class DestinationResolver:
    """Resolve and validate a platform chat id from Chatwoot metadata."""

    @staticmethod
    def looks_like_uuid(value: str) -> bool:
        return bool(re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", str(value or "").strip(), flags=re.IGNORECASE))

    @staticmethod
    def split_prefixed_source_id(value: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        raw = str(value or "").strip()
        if not raw or ":" not in raw:
            return None, None
        prefix, remainder = raw.split(":", 1)
        prefix, remainder = str(prefix or "").strip().upper(), str(remainder or "").strip()
        return (prefix, remainder) if prefix and remainder else (None, None)

    @classmethod
    def extract_destination(cls, payload: dict[str, Any], *, expected_prefix: Optional[str], known_prefixes: set[str]) -> tuple[Optional[str], Optional[str]]:
        conversation = payload.get("conversation") if isinstance(payload.get("conversation"), dict) else {}
        contact_inbox = conversation.get("contact_inbox") if isinstance(conversation.get("contact_inbox"), dict) else {}
        sender_meta = (conversation.get("meta") or {}).get("sender") if isinstance(conversation.get("meta"), dict) else {}
        source_id = str(contact_inbox.get("source_id") or "").strip() or None
        identifier = str(sender_meta.get("identifier") or "").strip() or None
        for raw in (source_id, identifier):
            prefix, value = cls.split_prefixed_source_id(raw)
            if prefix and value and expected_prefix and prefix == expected_prefix:
                return value, raw
        for raw in (source_id, identifier):
            prefix, value = cls.split_prefixed_source_id(raw)
            if prefix and value and not expected_prefix and prefix in known_prefixes:
                return value, raw
        for raw in (source_id, identifier):
            prefix, _ = cls.split_prefixed_source_id(raw)
            if prefix and expected_prefix and prefix != expected_prefix:
                continue
            if prefix and not expected_prefix and prefix in known_prefixes:
                continue
            if raw and not cls.looks_like_uuid(raw):
                return raw, raw
        if source_id:
            prefix, _ = cls.split_prefixed_source_id(source_id)
            if prefix and expected_prefix and prefix != expected_prefix:
                return None, None
            return source_id, source_id
        if identifier:
            prefix, _ = cls.split_prefixed_source_id(identifier)
            if prefix and expected_prefix and prefix != expected_prefix:
                return None, None
            return identifier, identifier
        return None, None

    @staticmethod
    def choose_chat_id(mapped_destination: Optional[str], extracted_destination: Optional[str]) -> Optional[str]:
        mapped, extracted = str(mapped_destination or "").strip() or None, str(extracted_destination or "").strip() or None
        if mapped and not DestinationResolver.looks_like_uuid(mapped):
            return mapped
        if extracted and not DestinationResolver.looks_like_uuid(extracted):
            return extracted
        return extracted or mapped
