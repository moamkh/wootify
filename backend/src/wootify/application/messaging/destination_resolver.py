"""Destination selection policies for Chatwoot-originated messages."""
from __future__ import annotations

import re
from typing import Any, Optional


class DestinationResolver:
    """Resolve and validate a platform chat id from Chatwoot metadata."""

    PEER_TYPE_TOKENS = frozenset({"USER", "GROUP", "CHANNEL"})

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

    @staticmethod
    def prefix_namespace(prefix: Optional[str]) -> Optional[str]:
        """Return the protocol namespace shared by connector variants.

        ``BALE``, ``BALE_PV`` and ``BALE_ENTERPRISE`` all address Bale peer
        IDs, while Telegram/Eitaa/Instagram identifiers live in separate ID
        namespaces. This lets an inbox safely reuse a contact created by a
        sibling connector without ever forwarding a foreign composite string
        to a numeric protocol client.
        """
        value = str(prefix or "").strip().upper()
        return value.split("_", 1)[0] if value else None

    @classmethod
    def parse_platform_identifier(
        cls,
        value: Optional[str],
        *,
        known_prefixes: set[str],
    ) -> dict[str, Optional[str]]:
        """Parse simple, typed, and enterprise Wootify identifiers.

        Supported forms include ``EITAA_PV:<peer>``,
        ``BALE_PV:GROUP:<peer>`` and
        ``TELEGRAM_ENTERPRISE:<instance>:<peer>``. Unknown prefixes are left
        untouched so identifiers owned by other integrations remain foreign.
        """
        raw = str(value or "").strip()
        prefix, remainder = cls.split_prefixed_source_id(raw)
        if not prefix or not remainder or prefix not in known_prefixes:
            return {
                "raw": raw or None,
                "prefix": None,
                "namespace": None,
                "instance_key": None,
                "peer_type": None,
                "peer_id": None,
            }

        parts = [part.strip() for part in remainder.split(":")]
        parts = [part for part in parts if part]
        peer_type: Optional[str] = None
        instance_key: Optional[str] = None
        peer_id: Optional[str] = remainder

        if parts and parts[0].upper() in cls.PEER_TYPE_TOKENS:
            peer_type = parts[0].lower()
            peer_id = ":".join(parts[1:]) or None
        elif prefix.endswith("_ENTERPRISE") and len(parts) >= 2:
            instance_key = ":".join(parts[:-1]) or None
            peer_id = parts[-1] or None

        return {
            "raw": raw,
            "prefix": prefix,
            "namespace": cls.prefix_namespace(prefix),
            "instance_key": instance_key,
            "peer_type": peer_type,
            "peer_id": peer_id,
        }

    @classmethod
    def compatible_platform_destination(
        cls,
        value: Optional[str],
        *,
        expected_prefix: Optional[str],
        known_prefixes: set[str],
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Return ``(peer_id, peer_type, source_prefix)`` when compatible."""
        parsed = cls.parse_platform_identifier(value, known_prefixes=known_prefixes)
        source_prefix = parsed.get("prefix")
        if not source_prefix:
            return None, None, None
        if cls.prefix_namespace(source_prefix) != cls.prefix_namespace(expected_prefix):
            return None, None, source_prefix
        return parsed.get("peer_id"), parsed.get("peer_type"), source_prefix

    @classmethod
    def extract_destination(cls, payload: dict[str, Any], *, expected_prefix: Optional[str], known_prefixes: set[str]) -> tuple[Optional[str], Optional[str]]:
        conversation = payload.get("conversation") if isinstance(payload.get("conversation"), dict) else {}
        contact_inbox = conversation.get("contact_inbox") if isinstance(conversation.get("contact_inbox"), dict) else {}
        sender_meta = (conversation.get("meta") or {}).get("sender") if isinstance(conversation.get("meta"), dict) else {}
        source_id = str(contact_inbox.get("source_id") or "").strip() or None
        identifier = str(sender_meta.get("identifier") or "").strip() or None
        for raw in (source_id, identifier):
            value, _, source_prefix = cls.compatible_platform_destination(
                raw,
                expected_prefix=expected_prefix,
                known_prefixes=known_prefixes,
            )
            if value and source_prefix and expected_prefix:
                return value, raw
        for raw in (source_id, identifier):
            parsed = cls.parse_platform_identifier(raw, known_prefixes=known_prefixes)
            if parsed.get("prefix") and parsed.get("peer_id") and not expected_prefix:
                return parsed["peer_id"], raw
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
