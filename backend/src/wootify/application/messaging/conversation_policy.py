"""Live, inbox-scoped conversation reuse policy shared by all Wootify bridges."""
from __future__ import annotations

from typing import Any


def policy_bool(value: Any) -> bool:
    """Treat explicit true values as enabled, never truthy strings like 'false'."""
    return value is True or value == 1 or str(value).strip().lower() in {"true", "yes", "on"}


def reopen_enabled(config: dict[str, Any], inbox_id: Any) -> bool:
    """An inbox override wins over the backwards-compatible instance default."""
    overrides = config.get("reopen_conversation_by_inbox") or {}
    if isinstance(overrides, dict) and str(inbox_id) in overrides:
        return policy_bool(overrides[str(inbox_id)])
    return policy_bool(config.get("reopen_conversation", False))


def select_conversation(rows: list[dict[str, Any]], inbox_id: Any, reopen: bool) -> dict[str, Any] | None:
    """Prefer an unresolved conversation; otherwise reopen the newest resolved one."""
    candidates = [row for row in rows if str(row.get("inbox_id")) == str(inbox_id)
                  and str(row.get("id") or '').isdigit()]
    candidates.sort(key=lambda row: int(row.get("id") or 0), reverse=True)
    for row in candidates:
        if str(row.get("status", "")).strip().lower() not in {"resolved", "closed", "1"}:
            return row
    return candidates[0] if reopen and candidates else None
