"""Chatwoot status/operator notification policy."""
from __future__ import annotations

import time
from typing import Any, Optional

from wootify.config import settings
from wootify.application.shared.cache import TTLCache


class NotificationPolicy:
    """Pure status templates plus short-lived delivery idempotency."""

    def __init__(self, recent: Optional[TTLCache[bool]] = None) -> None:
        self._recent = recent or TTLCache(maxsize=5000, ttl=300)

    @staticmethod
    def string_or_none(value: Any, *, fallback: str = "") -> Optional[str]:
        if value is None:
            text = str(fallback or "").strip()
            return text or None
        text = str(value).strip()
        return text or None

    @classmethod
    def normalize_status(cls, value: Any) -> Optional[str]:
        mapping = {0: "open", 1: "resolved", 2: "pending", 3: "snoozed"}
        if isinstance(value, bool): return None
        if isinstance(value, int): return mapping.get(value)
        text = str(value or "").strip().lower()
        if not text: return None
        if text.isdigit(): return mapping.get(int(text))
        return {
            "open": "open", "opened": "open", "reopened": "open", "conversation_opened": "open",
            "resolved": "resolved", "conversation_resolved": "resolved", "pending": "pending",
            "conversation_pending": "pending", "snoozed": "snoozed", "snooze": "snoozed",
            "conversation_snoozed": "snoozed", "unsnoozed": "open", "conversation_unsnoozed": "open",
        }.get(text)

    @classmethod
    def extract_changed_status(cls, value: Any) -> Optional[str]:
        if isinstance(value, dict):
            if "status" in value: return cls._extract_change_value(value.get("status"))
            for nested in value.values():
                status = cls.extract_changed_status(nested)
                if status: return status
        elif isinstance(value, list):
            for item in value:
                status = cls.extract_changed_status(item)
                if status: return status
        return None

    @classmethod
    def _extract_change_value(cls, value: Any) -> Optional[str]:
        if isinstance(value, list):
            return cls.normalize_status(value[-1]) if value else None
        if isinstance(value, dict):
            for key in ("new", "current", "to", "after", "value"):
                if key in value:
                    status = cls.normalize_status(value.get(key))
                    if status: return status
            for nested in value.values():
                status = cls._extract_change_value(nested)
                if status: return status
            return None
        return cls.normalize_status(value)

    @classmethod
    def extract_changed_status_from_payload(cls, payload: dict[str, Any]) -> Optional[str]:
        return cls.extract_changed_status(payload.get("changed_attributes"))

    @classmethod
    def status_event(cls, payload: dict[str, Any], event_name: str) -> bool:
        if event_name in {
            "conversation_status_changed", "conversation_resolved", "conversation_opened",
            "conversation_pending", "conversation_snoozed", "conversation_unsnoozed", "conversation_reopened",
        }:
            return True
        if event_name == "conversation_updated":
            return cls.extract_changed_status_from_payload(payload) is not None
        return not event_name and cls.extract_changed_status_from_payload(payload) is not None

    @staticmethod
    def enabled(platform_metadata: Optional[dict[str, Any]]) -> bool:
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        raw = cfg.get("chatwoot_status_notify_to_platform")
        if raw is None: raw = cfg.get("chatwoot_status_notify_to_bale")
        return bool(settings.CHATWOOT_STATUS_NOTIFY_TO_BALE) if raw is None else bool(raw)

    @classmethod
    def templates(cls, platform_metadata: Optional[dict[str, Any]]) -> dict[str, Optional[str]]:
        cfg = platform_metadata if isinstance(platform_metadata, dict) else {}
        return {
            "open": cls.string_or_none(cfg.get("chatwoot_status_message_open"), fallback=settings.CHATWOOT_STATUS_MESSAGE_OPEN),
            "open_by_operator": cls.string_or_none(cfg.get("chatwoot_status_message_open_by_operator"), fallback=settings.CHATWOOT_STATUS_MESSAGE_OPEN_BY_OPERATOR),
            "resolved": cls.string_or_none(cfg.get("chatwoot_status_message_resolved"), fallback=settings.CHATWOOT_STATUS_MESSAGE_RESOLVED),
            "pending": cls.string_or_none(cfg.get("chatwoot_status_message_pending"), fallback=settings.CHATWOOT_STATUS_MESSAGE_PENDING),
            "snoozed": cls.string_or_none(cfg.get("chatwoot_status_message_snoozed"), fallback=settings.CHATWOOT_STATUS_MESSAGE_SNOOZED),
        }

    @classmethod
    def text(cls, status_name: str, *, operator_name: Optional[str] = None, platform_metadata: Optional[dict[str, Any]] = None) -> Optional[str]:
        templates = cls.templates(platform_metadata)
        if status_name == "open" and operator_name and templates.get("open_by_operator"):
            template = templates["open_by_operator"]
            try: rendered = template.format(operator_name=operator_name)
            except Exception: rendered = f"Your chat has been opened by {operator_name}."
            if str(rendered or "").strip(): return str(rendered).strip()
        return str(templates.get(status_name) or "").strip() or None

    def is_duplicate(self, instance_key: str, conversation_id: str, status_name: str) -> bool:
        key = "|".join([str(instance_key), str(conversation_id), str(status_name)])
        previous = self._recent.get(key)
        return previous is not None and (time.monotonic() - float(previous)) <= 8.0

    def mark(self, instance_key: str, conversation_id: str, status_name: str) -> None:
        key = "|".join([str(instance_key), str(conversation_id), str(status_name)])
        self._recent.set(key, time.monotonic())
