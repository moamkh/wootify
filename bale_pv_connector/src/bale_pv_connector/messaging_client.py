"""
Bale Messaging Client (bale.messaging.v2.Messaging)
====================================================

Reverse-engineered from web.bale.ai WebSocket frame captures.

Uses WebSocket transport with custom Y/Z protobuf framing.

Methods
-------
- SendMessage(peer_id, text, reply_to_message_id=None)
- UpdateMessage(peer_id, message_id, text) → edit message
- MessageRead(peer_id, max_id) → mark messages as read
- StopTyping(peer_id) → stop typing indicator
"""

import logging
from typing import Any, Dict, List, Optional

from .messaging_messages import (
    SendMessageRequest,
    UpdateMessageRequest,
    DeleteMessageRequest,
    MessageReadRequest,
    StopTypingRequest,
    DocumentMessage,
    LoadDialogsRequest,
    LoadGroupsRequest,
    LoadHistoryRequest,
    LoadUsersRequest,
    ImportContactsRequest,
    PhoneContact,
    GetContactsRequest,
)
from .ws_client import BaleWebSocketClient

logger = logging.getLogger("bale_pv_connector.messaging")


class BaleMessagingClient:
    """Client for Bale messaging service (bale.messaging.v2.Messaging)."""

    SERVICE = "bale.messaging.v2.Messaging"

    def __init__(
        self,
        jwt_token: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        update_queue: Optional[Any] = None,
    ):
        self.ws = BaleWebSocketClient(
            jwt_token=jwt_token,
            metadata=metadata,
            on_message=self._on_message,
            on_update=self._on_update,
            update_queue=update_queue,
        )
        self._last_message_handler: Optional[Any] = None

    def _on_message(self, data: Optional[bytes]) -> None:
        if data is None:
            logger.debug("Received message response: None")
            return
        logger.debug("Received message response: %s bytes", len(data))

    def _on_update(self, data: bytes) -> None:
        logger.debug("Received update: %s bytes", len(data))
        if self._last_message_handler:
            try:
                self._last_message_handler(data)
            except Exception:
                logger.exception("Update handler error")

    async def connect(self) -> None:
        """Connect WebSocket and perform handshake."""
        await self.ws.connect()

    async def close(self) -> None:
        """Close connection."""
        await self.ws.close()

    async def send_message(
        self,
        peer_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None,
        access_hash: Optional[int] = None,
    ) -> None:
        """Send a text message to a peer (fire-and-forget).

        Bale server acknowledges SendMessage via an update, not a response.
        Using send_update avoids waiting for a response that never arrives.
        """
        req = SendMessageRequest(
            peer_id=peer_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
            access_hash=access_hash,
        )
        await self.ws.send_update(
            service_name=self.SERVICE,
            method="SendMessage",
            payload=req.serialize(),
        )

    async def update_message(
        self,
        peer_id: int,
        message_id: int,
        text: str,
    ) -> bytes:
        """Edit an existing message.

        Returns the raw protobuf response bytes.
        """
        req = UpdateMessageRequest(
            peer_id=peer_id,
            message_id=message_id,
            text=text,
        )
        return await self.ws.send_request(
            service_name=self.SERVICE,
            method="UpdateMessage",
            payload=req.serialize(),
        )

    async def delete_message(
        self,
        peer_id: int,
        message_ids: List[int],
    ) -> bytes:
        """Delete one or more messages.

        Returns the raw protobuf response bytes.
        """
        req = DeleteMessageRequest(
            peer_id=peer_id,
            message_ids=message_ids,
        )
        return await self.ws.send_request(
            service_name=self.SERVICE,
            method="DeleteMessage",
            payload=req.serialize(),
        )

    async def message_read(
        self,
        peer_id: int,
        max_id: int,
    ) -> bytes:
        """Mark messages as read up to max_id.

        Returns the raw protobuf response bytes.
        """
        req = MessageReadRequest(
            peer_id=peer_id,
            max_id=max_id,
        )
        return await self.ws.send_request(
            service_name=self.SERVICE,
            method="MessageRead",
            payload=req.serialize(),
        )

    async def stop_typing(self, peer_id: int) -> None:
        """Send stop typing indicator (fire-and-forget)."""
        req = StopTypingRequest(peer_id=peer_id)
        await self.ws.send_update(
            service_name=self.SERVICE,
            method="StopTyping",
            payload=req.serialize(),
        )

    async def load_dialogs(
        self,
        limit: int = 100,
        min_date: int = 0,
        dialog_type: int = 0,
        exclude_pinned: bool = False,
        optimizations: Optional[List[int]] = None,
    ) -> bytes:
        """Fetch dialogs list (response bytes)."""
        req = LoadDialogsRequest(
            limit=limit,
            min_date=min_date,
            dialog_type=dialog_type,
            exclude_pinned=exclude_pinned,
            optimizations=optimizations,
        )
        return await self.ws.send_request(
            service_name=self.SERVICE,
            method="LoadDialogs",
            payload=req.serialize(),
        )

    async def load_history(
        self,
        peer_id: int,
        peer_type: int = 1,
        date: int = 0,
        limit: int = 50,
        load_mode: int = 2,
    ) -> bytes:
        """Fetch message history for a peer (response bytes)."""
        req = LoadHistoryRequest(
            peer_id=peer_id,
            peer_type=peer_type,
            date=date,
            limit=limit,
            load_mode=load_mode,
        )
        return await self.ws.send_request(
            service_name=self.SERVICE,
            method="LoadHistory",
            payload=req.serialize(),
        )

    async def load_users(self, user_peers: list[dict[str, int]]) -> bytes:
        """Fetch user details for the given peers (response bytes)."""
        req = LoadUsersRequest(user_peers)
        return await self.ws.send_request(
            service_name="bale.users.v1.Users",
            method="LoadUsers",
            payload=req.serialize(),
        )

    async def load_groups(self, group_peers: List[Dict[str, int]]) -> bytes:
        """Fetch group/channel details for the given peers (response bytes)."""
        req = LoadGroupsRequest(group_peers)
        return await self.ws.send_request(
            service_name="bale.groups.v1.Groups",
            method="LoadGroups",
            payload=req.serialize(),
        )

    async def send_document(
        self,
        peer_id: int,
        file_id: int,
        file_access_hash: int,
        file_size: int,
        name: str,
        mime_type: str,
        caption: Optional[str] = None,
        reply_to_message_id: Optional[int] = None,
        thumb: Optional[Any] = None,
        ext: Optional[Any] = None,
        peer_access_hash: int = 0,
    ) -> None:
        """Send a document/media message (fire-and-forget).

        ``file_access_hash`` is the access_hash returned by
        GetNasimFileUploadUrl for this specific file and belongs in the
        DocumentMessage.  ``peer_access_hash`` is the recipient peer's
        access_hash (used to build an ExPeer in SendMessageRequest) and is
        kept separate so the server can authenticate both the file reference
        and the target peer independently.
        """
        doc = DocumentMessage(
            file_id=file_id,
            access_hash=file_access_hash,
            file_size=file_size,
            name=name,
            mime_type=mime_type,
            caption=caption,
            thumb=thumb,
            ext=ext,
        )
        req = SendMessageRequest(
            peer_id=peer_id,
            document=doc.serialize(),
            reply_to_message_id=reply_to_message_id,
            access_hash=peer_access_hash or None,
        )
        await self.ws.send_update(
            service_name=self.SERVICE,
            method="SendMessage",
            payload=req.serialize(),
        )

    async def import_contacts(
        self,
        phones: List[Dict[str, Any]],
        optimizations: Optional[List[int]] = None,
    ) -> bytes:
        """Import phone contacts and return resolved user details.

        Each phone entry should be a dict with keys:
          - phone_number (int or str)
          - name (optional str)
        """
        contacts = []
        for entry in phones:
            pn = entry["phone_number"]
            if isinstance(pn, str):
                pn = int(pn.replace("+", "").replace(" ", ""))
            contacts.append(PhoneContact(phone_number=pn, name=entry.get("name")))
        req = ImportContactsRequest(phones=contacts, optimizations=optimizations)
        return await self.ws.send_request(
            service_name="bale.users.v1.Users",
            method="ImportContacts",
            payload=req.serialize(),
        )

    async def get_contacts(
        self,
        contacts_hash: str = "",
        optimizations: Optional[List[int]] = None,
    ) -> bytes:
        """Fetch the authenticated account's contact list (response bytes)."""
        from .messaging_messages import GetContactsRequest
        req = GetContactsRequest(contacts_hash=contacts_hash, optimizations=optimizations)
        return await self.ws.send_request(
            service_name="bale.users.v1.Users",
            method="GetContacts",
            payload=req.serialize(),
        )
