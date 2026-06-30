"""
High-level async client for Bale Messenger.

This module wraps the lower-level auth, WebSocket, messaging, groups and user
clients into an ergonomic API similar to popular Telegram clients.

Example::

    import asyncio
    from bale_pv_connector import BaleClient

    async def main():
        client = BaleClient()
        await client.start_phone_auth("989123456789")
        code = input("SMS code: ")
        await client.validate_code(code)
        await client.connect()

        async for update in client.get_updates():
            print(update)

    asyncio.run(main())
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional

from .auth_client import BaleAuthClient
from .base_client import DEFAULT_METADATA
from .dialog_parser import (
    parse_get_contacts_response,
    parse_load_dialogs_response,
    parse_load_history_response,
    parse_load_users_response,
)
from .exceptions import BaleAuthError, BaleConnectionError
from .groups_client import BaleGroupsClient
from .messaging_client import BaleMessagingClient
from .update_parser import parse_ws_update

logger = logging.getLogger("bale_pv_connector")


@dataclass
class AuthResult:
    """Result of a successful phone-auth validation."""

    jwt: str
    user_raw: Optional[bytes] = None
    config_raw: Optional[bytes] = None


class BaleClient:
    """Async-first high-level client for Bale Messenger."""

    def __init__(
        self,
        jwt_token: Optional[str] = None,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ):
        self.metadata = {**DEFAULT_METADATA, **(metadata or {})}
        if session_id:
            self.metadata["session_id"] = session_id
            self.metadata["mt_session_id"] = session_id

        self._jwt_token: Optional[str] = jwt_token
        self._last_transaction_hash: str = ""
        self._auth_client = BaleAuthClient(metadata=self.metadata)
        self._messaging_client: Optional[BaleMessagingClient] = None
        self._groups_client: Optional[BaleGroupsClient] = None
        self._update_queue: asyncio.Queue[bytes] = asyncio.Queue()

        if jwt_token:
            self._set_jwt(jwt_token)

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    async def start_phone_auth(
        self,
        phone_number: str,
        device_title: str = "bale_pv_connector",
        send_code_type: int = 0,
    ) -> Dict[str, Any]:
        """Request an SMS code for ``phone_number``.

        Returns a dict containing ``transaction_hash`` and other fields.
        """
        result = await self._auth_client.start_phone_auth(
            phone_number=phone_number,
            device_title=device_title,
            send_code_type=send_code_type,
        )
        self._last_transaction_hash = result.get("transaction_hash", "")
        return result

    async def validate_code(
        self,
        code: str,
        transaction_hash: Optional[str] = None,
    ) -> AuthResult:
        """Validate the SMS code and obtain a JWT.

        If ``transaction_hash`` is omitted, the last value returned by
        :meth:`start_phone_auth` is used.
        """
        if transaction_hash is None:
            transaction_hash = getattr(self, "_last_transaction_hash", "")
        if not transaction_hash:
            raise BaleAuthError("transaction_hash is required")

        result = await self._auth_client.validate_code(
            transaction_hash=transaction_hash,
            code=code,
        )
        jwt = result.get("jwt")
        if not jwt:
            raise BaleAuthError("ValidateCode did not return a JWT")
        self._set_jwt(jwt)
        return AuthResult(
            jwt=jwt,
            user_raw=result.get("user_raw"),
            config_raw=result.get("config_raw"),
        )

    def _set_jwt(self, jwt_token: str) -> None:
        self._jwt_token = jwt_token
        self._auth_client.set_jwt_token(jwt_token)
        self._messaging_client = BaleMessagingClient(
            jwt_token=jwt_token,
            metadata=self.metadata,
            update_queue=self._update_queue,
        )
        self._groups_client = BaleGroupsClient(self._messaging_client.ws)

    @property
    def jwt_token(self) -> Optional[str]:
        return self._jwt_token

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        """Open the WebSocket connection (requires a valid JWT)."""
        if not self._messaging_client:
            raise BaleConnectionError(
                "No JWT token. Authenticate with start_phone_auth -> validate_code first."
            )
        await self._messaging_client.connect()
        logger.info("BaleClient connected")

    async def close(self) -> None:
        """Close the WebSocket connection."""
        if self._messaging_client:
            await self._messaging_client.close()
        await self._auth_client.close()

    async def __aenter__(self) -> "BaleClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    @property
    def is_connected(self) -> bool:
        if not self._messaging_client:
            return False
        return self._messaging_client.ws.is_connected

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------
    async def get_dialogs(
        self,
        limit: int = 100,
        exclude_pinned: bool = False,
        dialog_type: int = 0,
    ) -> Dict[str, Any]:
        """Fetch the conversation list.

        Returns a dict with ``dialogs``, ``users``, ``groups``,
        ``user_peers`` and ``group_peers``.
        """
        if not self._messaging_client:
            raise BaleConnectionError("Client is not connected/authenticated")
        raw = await self._messaging_client.load_dialogs(
            limit=limit,
            exclude_pinned=exclude_pinned,
            dialog_type=dialog_type,
        )
        return parse_load_dialogs_response(raw)

    async def get_groups(
        self,
        optimizations: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch the group/channel list."""
        if not self._groups_client:
            raise BaleConnectionError("Client is not connected/authenticated")
        return await self._groups_client.load_groups(optimizations=optimizations)

    async def get_history(
        self,
        peer_id: int,
        peer_type: int = 1,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Fetch message history for a peer."""
        if not self._messaging_client:
            raise BaleConnectionError("Client is not connected/authenticated")
        raw = await self._messaging_client.load_history(
            peer_id=peer_id,
            peer_type=peer_type,
            limit=limit,
        )
        return parse_load_history_response(raw)

    async def get_users(
        self,
        user_peers: List[Dict[str, int]],
    ) -> Dict[str, Any]:
        """Resolve user details for the given peer list.

        Each peer should be a dict with ``uid`` and optionally
        ``access_hash``.
        """
        if not self._messaging_client:
            raise BaleConnectionError("Client is not connected/authenticated")
        raw = await self._messaging_client.load_users(user_peers)
        return parse_load_users_response(raw)

    async def get_contacts(
        self,
        contacts_hash: str = "",
    ) -> Dict[str, Any]:
        """Fetch the authenticated account's contact list."""
        if not self._messaging_client:
            raise BaleConnectionError("Client is not connected/authenticated")
        raw = await self._messaging_client.get_contacts(contacts_hash=contacts_hash)
        return parse_get_contacts_response(raw)

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------
    async def send_message(
        self,
        peer_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None,
        access_hash: Optional[int] = None,
    ) -> None:
        """Send a text message to a peer."""
        if not self._messaging_client:
            raise BaleConnectionError("Client is not connected/authenticated")
        await self._messaging_client.send_message(
            peer_id=peer_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
            access_hash=access_hash,
        )

    async def edit_message(
        self,
        peer_id: int,
        message_id: int,
        text: str,
    ) -> bytes:
        """Edit an existing message."""
        if not self._messaging_client:
            raise BaleConnectionError("Client is not connected/authenticated")
        return await self._messaging_client.update_message(
            peer_id=peer_id,
            message_id=message_id,
            text=text,
        )

    async def delete_messages(
        self,
        peer_id: int,
        message_ids: List[int],
    ) -> bytes:
        """Delete one or more messages."""
        if not self._messaging_client:
            raise BaleConnectionError("Client is not connected/authenticated")
        return await self._messaging_client.delete_message(peer_id, message_ids)

    async def mark_read(
        self,
        peer_id: int,
        max_id: int,
    ) -> bytes:
        """Mark messages as read up to ``max_id``."""
        if not self._messaging_client:
            raise BaleConnectionError("Client is not connected/authenticated")
        return await self._messaging_client.message_read(peer_id, max_id)

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------
    async def get_updates(self) -> AsyncIterator[Dict[str, Any]]:
        """Async iterator over incoming server updates.

        Updates are parsed into readable dicts. Unknown or unparseable
        frames are skipped.
        """
        if not self._messaging_client:
            raise BaleConnectionError("Client is not connected/authenticated")
        while True:
            raw = await self._update_queue.get()
            parsed = parse_ws_update(raw)
            if parsed is not None:
                yield parsed
