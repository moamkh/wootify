"""Pure policies shared by the Bale and Telegram Enterprise workflows.

The Enterprise services still own transport and persistence orchestration.  The
objects in this module only resolve configuration or apply a small, explicit
state transition through a repository supplied by the caller.  Keeping those
decisions here makes the two platform services easier to reason about without
coupling their platform-specific models or payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import mimetypes
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class EnterpriseRoutePolicy:
    """Resolve either static or metadata-defined Enterprise routes.

    Bale uses a static route table whose values point to metadata keys, while
    Telegram stores complete route dictionaries under ``enterprise_routes``.
    Both forms are supported without normalizing or mutating the source data.
    """

    static_routes: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    metadata_key: str = "enterprise_routes"
    text_key_map: Mapping[str, str] = field(default_factory=dict)

    def get(
        self,
        platform_metadata: Optional[Mapping[str, Any]],
        route_key: str,
    ) -> Optional[dict[str, Any]]:
        """Return a route mapping by key, preserving the configured shape."""
        normalized_key = str(route_key or "").strip()
        if self.static_routes:
            route = self.static_routes.get(normalized_key)
            return route if isinstance(route, dict) else (dict(route) if route is not None else None)

        metadata = platform_metadata if isinstance(platform_metadata, Mapping) else {}
        routes = metadata.get(self.metadata_key) or []
        for route in routes:
            if isinstance(route, Mapping) and route.get("route_key") == route_key:
                return route if isinstance(route, dict) else dict(route)
        return None

    def require(
        self,
        platform_metadata: Optional[Mapping[str, Any]],
        route_key: str,
    ) -> dict[str, Any]:
        """Return a configured route or raise the established public error."""
        route = self.get(platform_metadata, route_key)
        if not route:
            raise ValueError(f"unsupported enterprise route {route_key}")
        return route

    def text(
        self,
        platform_metadata: Optional[Mapping[str, Any]],
        route_key: str,
        kind: str,
    ) -> Optional[str]:
        """Resolve a route message, returning ``None`` when it is absent."""
        route = self.get(platform_metadata, route_key)
        if not route:
            return None
        field_key = self.text_key_map.get(kind)
        if self.static_routes:
            if not field_key:
                return None
            metadata = platform_metadata if isinstance(platform_metadata, Mapping) else {}
            metadata_field = route[field_key]
            text = str(metadata.get(metadata_field) or "").strip()
        else:
            field_name = field_key or f"{kind}_text"
            text = str(route.get(field_name) or "").strip()
        return text or None

    def match_display_name(
        self,
        platform_metadata: Optional[Mapping[str, Any]],
        display_name: str,
    ) -> Optional[dict[str, Any]]:
        """Find a metadata route whose visible name matches user input."""
        metadata = platform_metadata if isinstance(platform_metadata, Mapping) else {}
        routes = metadata.get(self.metadata_key) or []
        normalized = str(display_name or "").strip()
        for route in routes:
            if isinstance(route, Mapping) and str(route.get("display_name") or "").strip() == normalized:
                return route if isinstance(route, dict) else dict(route)
        return None


@dataclass(frozen=True)
class EnterpriseMenuConfig:
    """Resolve configurable Enterprise labels and message text."""

    not_configured_field: str = "enterprise_not_configured_text"

    @staticmethod
    def _metadata(platform_metadata: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
        return platform_metadata if isinstance(platform_metadata, Mapping) else {}

    def message(
        self,
        platform_metadata: Optional[Mapping[str, Any]],
        field_name: str,
        default: str,
    ) -> str:
        """Resolve a message override with its exact static fallback."""
        text = str(self._metadata(platform_metadata).get(field_name) or "").strip()
        return text or str(default or "").strip()

    def label(
        self,
        platform_metadata: Optional[Mapping[str, Any]],
        field_name: str,
        default: str,
    ) -> str:
        """Resolve a button label override with its exact static fallback."""
        return self.message(platform_metadata, field_name, default)

    def not_configured(self, platform_metadata: Optional[Mapping[str, Any]], default: str) -> str:
        """Resolve the configured missing-content fallback message."""
        return self.message(platform_metadata, self.not_configured_field, default)


class EnterpriseChatwootPayloadPolicy:
    """Extract and classify the Chatwoot payload shape shared by both bots."""

    @staticmethod
    def normalize_content_type(*, filename: str, content_type: Optional[str], content: bytes) -> Optional[str]:
        """Resolve a usable MIME type from a hint, filename, or file signature."""
        raw = str(content_type or "").strip().lower()
        if raw and raw != "application/octet-stream":
            return raw
        guessed = mimetypes.guess_type(str(filename or "").strip())[0]
        if guessed:
            return guessed.lower()
        if content.startswith(b"%PDF"):
            return "application/pdf"
        if content.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if content.startswith(b"OggS"):
            return "audio/ogg"
        if len(content) > 8 and content[4:8] == b"ftyp":
            return "video/mp4"
        return None

    @staticmethod
    def extract_conversation_id(payload: dict[str, Any]) -> Optional[str]:
        """Extract a Chatwoot conversation id from a webhook payload."""
        conversation = payload.get("conversation") if isinstance(payload.get("conversation"), dict) else {}
        cid = conversation.get("id") or payload.get("conversation_id") or payload.get("conversationId")
        if cid is None:
            return None
        text = str(cid).strip()
        return text or None

    @staticmethod
    def extract_message_id(payload: dict[str, Any]) -> Optional[str]:
        """Extract a Chatwoot message id from a webhook payload."""
        message_obj = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        candidate = payload.get("id")
        if candidate is None:
            candidate = message_obj.get("id")
        if candidate is None:
            return None
        text = str(candidate).strip()
        return text or None

    @staticmethod
    def extract_message_text(payload: dict[str, Any]) -> str:
        """Extract outbound Chatwoot message text."""
        message_obj = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        candidates = [
            payload.get("content"),
            message_obj.get("content"),
            payload.get("processed_message_content"),
            message_obj.get("processed_message_content"),
        ]
        for value in candidates:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def extract_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract outbound Chatwoot attachments."""
        direct = payload.get("attachments")
        if isinstance(direct, list):
            return [item for item in direct if isinstance(item, dict)]
        nested = (payload.get("message") or {}).get("attachments")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        return []

    @staticmethod
    def normalize_message_type(value: Any) -> str:
        """Normalize Chatwoot message_type values across enum/string shapes."""
        if isinstance(value, int):
            return {0: "incoming", 1: "outgoing", 2: "activity", 3: "template"}.get(value, str(value))
        return str(value or "").strip().lower()

    @classmethod
    def is_forwardable(cls, payload: dict[str, Any], event_name: str) -> bool:
        """Return whether an outgoing/template webhook should reach a bot."""
        message_obj = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        message_type = cls.normalize_message_type(payload.get("message_type"))
        nested_type = cls.normalize_message_type(message_obj.get("message_type"))
        event = str(event_name or "").strip().lower()
        if message_type == "outgoing" or nested_type == "outgoing":
            return True
        return event == "message_created" and (message_type == "template" or nested_type == "template")


@dataclass(frozen=True)
class EnterpriseSessionTransitionPolicy:
    """Apply shared live-session state transitions through a repository factory."""

    non_live_states: frozenset[Any]
    route_by_state: Mapping[Any, str] = field(default_factory=dict)
    closed_status: Any = None

    def is_live_state(self, state: Any) -> bool:
        """Return whether ``state`` represents an active live route."""
        return state not in self.non_live_states and state not in ("", None)

    def active_session(self, repository_factory: Any, db: Any, user: Any) -> Any:
        """Find the unresolved session represented by the user’s current state."""
        state = user.current_state
        if not self.is_live_state(state):
            return None
        # A platform with an explicit state map (Bale) only treats mapped
        # states as live.  Telegram intentionally has no map because its
        # metadata may define arbitrary route keys.
        route_key = self.route_by_state.get(state) if self.route_by_state else state
        if route_key is None:
            return None
        return repository_factory(db).get_unresolved_for_user_route(user.id, route_key)

    def close_session(self, repository_factory: Any, db: Any, session: Any) -> None:
        """Close a user-abandoned session and persist the transition."""
        session.user_present = False
        if self.closed_status is not None:
            session.status = self.closed_status
        repository_factory(db).save(session)

    @staticmethod
    def mark_user_present(repository_factory: Any, db: Any, session: Any) -> None:
        """Mark a live session present only when it was previously absent."""
        if not session.user_present:
            session.user_present = True
            repository_factory(db).save(session)

    @staticmethod
    def set_user_state(repository_factory: Any, db: Any, user: Any, state: Any) -> None:
        """Persist a user state without coercing platform enum/string values."""
        user.current_state = state
        repository_factory(db).save(user)
