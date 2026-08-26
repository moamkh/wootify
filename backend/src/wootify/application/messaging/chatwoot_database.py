"""
Module Overview
---------------
Purpose: Direct PostgreSQL access to Chatwoot for operations the REST API
         does not support (message content edits, direct deletes).

Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

import asyncpg

logger = logging.getLogger(__name__)


class ChatwootDatabaseService:
    """Direct PostgreSQL accessor for Chatwoot.

    This bypasses the REST API for operations that are not exposed,
    such as editing the content of an already-sent message.
    """

    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
    ) -> None:
        """Initialize with connection parameters."""
        self._dsn = f"postgresql://{user}:{password}@{host}:{port}/{database}"
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        """Create the connection pool."""
        if self._pool is not None:
            return
        try:
            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=1,
                max_size=5,
                command_timeout=10,
            )
            logger.info("chatwoot_db connected")
        except Exception as exc:
            logger.exception("chatwoot_db connection failed: %s", exc)
            raise

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("chatwoot_db closed")

    # ------------------------------------------------------------------
    # Message operations
    # ------------------------------------------------------------------

    async def update_message_content(
        self,
        message_id: int,
        new_content: str,
        processed_content: Optional[str] = None,
    ) -> bool:
        """Edit a message's content directly in the DB.

        Chatwoot stores raw content in ``messages.content`` and the
        rendered / truncated version in ``messages.processed_message_content``.
        Both must be updated to keep the UI consistent.

        Args:
            message_id: The numeric Chatwoot message id.
            new_content: New text content.
            processed_content: Optional pre-computed processed content.
                If omitted, ``new_content`` is truncated to 150000 chars.

        Returns:
            True if a row was updated.
        """
        if not self._pool:
            raise RuntimeError("chatwoot_db not connected")

        processed = processed_content or new_content[:150_000]

        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE messages
                SET content = $1,
                    processed_message_content = $2,
                    updated_at = NOW()
                WHERE id = $3
                """,
                new_content,
                processed,
                message_id,
            )
            # asyncpg returns e.g. "UPDATE 1"
            affected = int(result.split()[-1]) if result else 0
            logger.info(
                "chatwoot_db update_message_content message_id=%s affected=%s",
                message_id,
                affected,
            )
            return affected > 0

    async def delete_message_hard(
        self,
        message_id: int,
    ) -> bool:
        """Hard-delete a message row (use with caution).

        Chatwoot's Application API ``destroy`` action performs a
        soft-delete by setting ``content_attributes['deleted'] = true``.
        This method *actually* removes the row, which is useful when
        you need the message to disappear completely from the DB.

        Returns:
            True if a row was deleted.
        """
        if not self._pool:
            raise RuntimeError("chatwoot_db not connected")

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Delete attachments first to avoid FK issues
                await conn.execute(
                    "DELETE FROM attachments WHERE message_id = $1",
                    message_id,
                )
                result = await conn.execute(
                    "DELETE FROM messages WHERE id = $1",
                    message_id,
                )
                affected = int(result.split()[-1]) if result else 0
                logger.info(
                    "chatwoot_db delete_message_hard message_id=%s affected=%s",
                    message_id,
                    affected,
                )
                return affected > 0

    async def get_message_by_id(
        self,
        message_id: int,
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single message row."""
        if not self._pool:
            raise RuntimeError("chatwoot_db not connected")

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, content, content_type, message_type,
                       content_attributes, conversation_id,
                       account_id, inbox_id, created_at, updated_at
                FROM messages
                WHERE id = $1
                """,
                message_id,
            )
            if row is None:
                return None
            return dict(row)

    async def list_messages_since(
        self,
        conversation_id: int,
        since_id: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List messages for a conversation, optionally filtered by id."""
        if not self._pool:
            raise RuntimeError("chatwoot_db not connected")

        async with self._pool.acquire() as conn:
            if since_id is not None:
                rows = await conn.fetch(
                    """
                    SELECT id, content, content_type, message_type,
                           content_attributes, conversation_id,
                           account_id, inbox_id, created_at, updated_at
                    FROM messages
                    WHERE conversation_id = $1
                      AND id > $2
                    ORDER BY id DESC
                    LIMIT $3
                    """,
                    conversation_id,
                    since_id,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, content, content_type, message_type,
                           content_attributes, conversation_id,
                           account_id, inbox_id, created_at, updated_at
                    FROM messages
                    WHERE conversation_id = $1
                    ORDER BY id DESC
                    LIMIT $2
                    """,
                    conversation_id,
                    limit,
                )
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Content-hash helpers for edit detection
    # ------------------------------------------------------------------

    @staticmethod
    def content_hash(content: Optional[str]) -> str:
        """Return a stable hash of message content for change detection."""
        text = str(content or "")
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
