"""
Module Overview
---------------
Purpose: Service-layer business logic for connector and synchronization workflows.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import MessageDirection, MessageKind, MessageMapping, MessageStatus
from app.repositories.message_mapping_repository import MessageMappingRepository

logger = logging.getLogger('app.services.message_mapping')


class MessageMappingService:
    """Service for message mapping domain workflows."""
    def __init__(self) -> None:
        """Initialize the instance."""
        self._repo_cls = MessageMappingRepository

    def list_for_conversation(self, db: Session, conversation_id: str) -> list[MessageMapping]:
        """List for conversation."""
        try:
            return self._repo_cls(db).list_by_conversation(conversation_id)
        except Exception:
            logger.exception('list_for_conversation failed conversation_id=%s', conversation_id)
            raise

    def get_by_chatwoot_message_id(
        self,
        db: Session,
        conversation_id: str,
        chatwoot_message_id: str,
    ) -> Optional[MessageMapping]:
        """Get by chatwoot message id."""
        try:
            return self._repo_cls(db).get_by_chatwoot_message_id(conversation_id, chatwoot_message_id)
        except Exception:
            logger.exception(
                'get_by_chatwoot_message_id failed conversation_id=%s chatwoot_message_id=%s',
                conversation_id,
                chatwoot_message_id,
            )
            raise

    def get_by_platform_message_id(
        self,
        db: Session,
        conversation_id: str,
        platform_message_id: str,
    ) -> Optional[MessageMapping]:
        """Get by platform message id."""
        try:
            return self._repo_cls(db).get_by_platform_message_id(conversation_id, platform_message_id)
        except Exception:
            logger.exception(
                'get_by_platform_message_id failed conversation_id=%s platform_message_id=%s',
                conversation_id,
                platform_message_id,
            )
            raise

    def find_platform_parent_for_chatwoot_parent(
        self,
        db: Session,
        conversation_id: str,
        chatwoot_parent_message_id: str,
    ) -> Optional[str]:
        """Find platform parent for chatwoot parent."""
        try:
            row = self.get_by_chatwoot_message_id(db, conversation_id, chatwoot_parent_message_id)
            if not row:
                return None
            return row.platform_message_id
        except Exception:
            logger.exception(
                'find_platform_parent_for_chatwoot_parent failed conversation_id=%s chatwoot_parent_message_id=%s',
                conversation_id,
                chatwoot_parent_message_id,
            )
            raise

    def find_chatwoot_parent_for_platform_parent(
        self,
        db: Session,
        conversation_id: str,
        platform_parent_message_id: str,
    ) -> Optional[str]:
        """Find chatwoot parent for platform parent."""
        try:
            row = self.get_by_platform_message_id(db, conversation_id, platform_parent_message_id)
            if not row:
                return None
            return row.chatwoot_message_id
        except Exception:
            logger.exception(
                'find_chatwoot_parent_for_platform_parent failed conversation_id=%s platform_parent_message_id=%s',
                conversation_id,
                platform_parent_message_id,
            )
            raise

    def upsert(
        self,
        db: Session,
        *,
        conversation_id: str,
        direction: MessageDirection,
        message_kind: MessageKind,
        status: MessageStatus,
        chatwoot_message_id: Optional[str] = None,
        platform_message_id: Optional[str] = None,
        chatwoot_parent_message_id: Optional[str] = None,
        platform_parent_message_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_detail: Optional[str] = None,
        chatwoot_payload_json: Optional[dict[str, Any]] = None,
        platform_payload_json: Optional[dict[str, Any]] = None,
    ) -> MessageMapping:
        """Create or update a message mapping row."""
        try:
            repo = self._repo_cls(db)

            row: Optional[MessageMapping] = None
            if chatwoot_message_id:
                row = repo.get_by_chatwoot_message_id(conversation_id, str(chatwoot_message_id))
            if not row and platform_message_id:
                row = repo.get_by_platform_message_id(conversation_id, str(platform_message_id))

            if not row:
                row = MessageMapping(conversation_id=str(conversation_id))

            row.direction = direction
            row.message_kind = message_kind
            row.status = status
            row.chatwoot_message_id = str(chatwoot_message_id) if chatwoot_message_id else None
            row.platform_message_id = str(platform_message_id) if platform_message_id else None
            row.chatwoot_parent_message_id = str(chatwoot_parent_message_id) if chatwoot_parent_message_id else None
            row.platform_parent_message_id = str(platform_parent_message_id) if platform_parent_message_id else None
            row.error_code = error_code
            row.error_detail = error_detail
            row.chatwoot_payload_json = chatwoot_payload_json
            row.platform_payload_json = platform_payload_json

            repo.save(row)
            return row
        except Exception:
            logger.exception(
                'message upsert failed conversation_id=%s chatwoot_message_id=%s platform_message_id=%s',
                conversation_id,
                chatwoot_message_id,
                platform_message_id,
            )
            raise

