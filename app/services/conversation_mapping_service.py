"""
Module Overview
---------------
Purpose: Service-layer business logic for connector and synchronization workflows.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.models import Conversation
from app.repositories.conversation_repository import ConversationRepository

logger = logging.getLogger('app.services.conversation_mapping')

_DB_RETRY = retry(
    retry=retry_if_exception_type(OperationalError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=10),
    reraise=True,
)


class ConversationMappingService:
    """Service for conversation mapping domain workflows."""
    def __init__(self) -> None:
        """Initialize the instance."""
        self._repo_cls = ConversationRepository

    def list_for_instance(self, db: Session, instance_id: str) -> list[Conversation]:
        """List for instance."""
        try:
            return self._repo_cls(db).list_by_instance(instance_id)
        except Exception:
            logger.exception('list_for_instance failed instance_id=%s', instance_id)
            raise

    def get_for_instance(self, db: Session, instance_id: str, conversation_id: str) -> Optional[Conversation]:
        """Get for instance."""
        try:
            row = self._repo_cls(db).get_by_id(conversation_id)
            if not row or row.instance_id != str(instance_id):
                return None
            return row
        except Exception:
            logger.exception(
                'get_for_instance failed instance_id=%s conversation_id=%s',
                instance_id,
                conversation_id,
            )
            raise

    def get_by_platform_id(self, db: Session, instance_id: str, platform_conversation_id: str) -> Optional[Conversation]:
        """Get by platform id."""
        try:
            return self._repo_cls(db).get_by_platform_id(instance_id, platform_conversation_id)
        except Exception:
            logger.exception(
                'get_by_platform_id failed instance_id=%s platform_conversation_id=%s',
                instance_id,
                platform_conversation_id,
            )
            raise

    def get_by_chatwoot_id(self, db: Session, instance_id: str, chatwoot_conversation_id: str) -> Optional[Conversation]:
        """Get by chatwoot id."""
        try:
            return self._repo_cls(db).get_by_chatwoot_id(instance_id, chatwoot_conversation_id)
        except Exception:
            logger.exception(
                'get_by_chatwoot_id failed instance_id=%s chatwoot_conversation_id=%s',
                instance_id,
                chatwoot_conversation_id,
            )
            raise

    def list_by_contact(
        self,
        db: Session,
        instance_id: str,
        chatwoot_contact_id: str,
        chatwoot_inbox_id: Optional[str] = None,
    ) -> list[Conversation]:
        """List by contact."""
        try:
            return self._repo_cls(db).list_by_contact(instance_id, chatwoot_contact_id, chatwoot_inbox_id)
        except Exception:
            logger.exception(
                'list_by_contact failed instance_id=%s chatwoot_contact_id=%s chatwoot_inbox_id=%s',
                instance_id,
                chatwoot_contact_id,
                chatwoot_inbox_id,
            )
            raise

    @_DB_RETRY
    def upsert(
        self,
        db: Session,
        *,
        instance_id: str,
        platform_conversation_id: str,
        chatwoot_conversation_id: str,
        chatwoot_contact_id: Optional[str] = None,
        chatwoot_inbox_id: Optional[str] = None,
    ) -> Conversation:
        """Create or update a conversation mapping row."""
        try:
            repo = self._repo_cls(db)
            current_row = repo.get_by_platform_id(instance_id, platform_conversation_id)
            row = repo.get_by_chatwoot_id(instance_id, chatwoot_conversation_id)
            if not row and current_row and str(current_row.chatwoot_conversation_id) == str(chatwoot_conversation_id):
                row = current_row

            if not row:
                row = Conversation(
                    instance_id=str(instance_id),
                    platform_conversation_id=str(platform_conversation_id),
                    chatwoot_conversation_id=str(chatwoot_conversation_id),
                    is_active=True,
                )

            row.platform_conversation_id = str(platform_conversation_id)
            row.chatwoot_conversation_id = str(chatwoot_conversation_id)
            row.chatwoot_contact_id = str(chatwoot_contact_id) if chatwoot_contact_id else None
            row.chatwoot_inbox_id = str(chatwoot_inbox_id) if chatwoot_inbox_id else None
            row.is_active = True
            row.last_activity_at = dt.datetime.utcnow()

            repo.save(row)
            repo.deactivate_platform_mappings(
                instance_id,
                platform_conversation_id,
                exclude_conversation_id=row.id,
            )
            return row
        except Exception:
            logger.exception(
                'conversation upsert failed instance_id=%s platform_conversation_id=%s chatwoot_conversation_id=%s',
                instance_id,
                platform_conversation_id,
                chatwoot_conversation_id,
            )
            raise

