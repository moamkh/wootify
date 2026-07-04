"""
Bale Messaging Protobuf Message Builders
=========================================

Reverse-engineered from WebSocket frame captures on web.bale.ai.

WebSocket Frame Structure (Client -> Server)
--------------------------------------------
Outer wrapper (ClientPack):
  Field 1 (bytes): Inner wrapper message

Inner wrapper:
  Field 1 (string): Service name  (e.g. "bale.messaging.v2.Messaging")
  Field 2 (string): Method name   (e.g. "SendMessage")
  Field 3 (bytes):  Request payload
  Field 4 (bytes):  Metadata message
  Field 5 (varint): Flags / timeout (always 25 in captures)

Metadata message:
  Field 1 (repeated message): Metadata entries
    Each entry:
      Field 1 (string): key
      Field 2 (message): StringValue wrapper { Field 1: value string }

SendMessage Payload:
  Field 1 (message): peer { Field 1: type (1=user), Field 2: id }
  Field 2 (varint):  random_id (client-generated int64)
  Field 3 (message): message { Field 15: textMessage { Field 1: text, Field 2: mentions } }
  Field 6 (message): peer (duplicate of Field 1)

UpdateMessage Payload (edit):
  Inferred: peer, message_id, new message content

MessageRead Payload:
  Inferred: peer, max_id

StopTyping Payload:
  peer

File Service (ai.bale.server.Files):
  GetNasimFileUrl request:
    Field 1 (message): peer
    Field 2 (message): file { Field 1: fileId, Field 2: accessHash, Field 3: fileStorageVersion }
    Field 3 (message): filename (StringValue wrapper)
  GetNasimFileUrls request:
    Field 1 (message): peer
    Field 2 (repeated message): files
  Response:
    Field 1 (message): fileUrl { Field 1: fileId, Field 2: url, Field 3: duplicate, Field 4: chunkSize, Field 5: blockSize }
"""

import random
import time
from typing import Dict, List, Optional, Union

from .protobuf_wire import ProtobufMessage


class StringValue:
    """google.protobuf.StringValue wrapper."""

    @staticmethod
    def serialize(value: str) -> bytes:
        msg = ProtobufMessage()
        msg.add_string(1, value)
        return msg.serialize()

    @staticmethod
    def parse(data: bytes) -> str:
        from .protobuf_wire import ProtobufParser
        fields = ProtobufParser(data).parse()
        val = fields.get(1, [b""])[0]
        return val.decode("utf-8") if isinstance(val, bytes) else str(val)


class Peer:
    """Peer identifier (user or group)."""

    PEER_TYPE_USER = 1
    PEER_TYPE_GROUP = 2
    PEER_TYPE_CHANNEL = 3

    def __init__(self, peer_id: int, peer_type: int = PEER_TYPE_USER):
        self.peer_id = peer_id
        self.peer_type = peer_type

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_int32(1, self.peer_type)
        msg.add_int64(2, self.peer_id)
        return msg.serialize()


class ExPeer:
    """Extended peer identifier for file operations."""

    def __init__(self, peer_id: int, peer_type: int = Peer.PEER_TYPE_USER, access_hash: int = 0):
        self.peer_id = peer_id
        self.peer_type = peer_type
        self.access_hash = access_hash

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_int32(1, self.peer_type)
        msg.add_int64(2, self.peer_id)
        msg.add_int64(3, self.access_hash)
        return msg.serialize()


class SendTypeValue:
    """Send type wrapper for file upload."""

    SEND_TYPE_UNKNOWN = 0
    SEND_TYPE_PHOTO = 1
    SEND_TYPE_VIDEO = 2
    SEND_TYPE_VOICE = 3
    SEND_TYPE_GIF = 4
    SEND_TYPE_AUDIO = 5
    SEND_TYPE_DOCUMENT = 6
    SEND_TYPE_STICKER = 7

    def __init__(self, send_type: int):
        self.send_type = send_type

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_int32(1, self.send_type)
        return msg.serialize()


class TextMessage:
    """Text message content."""

    def __init__(self, text: str):
        self.text = text

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_string(1, self.text)
        # Field 2 is an empty bytes field (mentions/formatting) in captures.
        # ProtobufMessage.add_bytes skips empty values, so append directly.
        msg._fields.append((2, 2, b""))
        return msg.serialize()


class MessageContent:
    """Message content wrapper (selects content type via field number)."""

    def __init__(self, text: Optional[str] = None, document: Optional[bytes] = None):
        self.text = text
        self.document = document

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        if self.text is not None:
            msg.add_message(15, TextMessage(self.text))
        if self.document is not None:
            msg.add_bytes(4, self.document)
        return msg.serialize()


class FastThumb:
    """FastThumb { width(1), height(2), thumb(3) }.

    A tiny JPEG/PNG thumbnail that the Bale client shows while the full
    media downloads.
    """

    def __init__(self, width: int, height: int, thumb: bytes):
        self.width = width
        self.height = height
        self.thumb = thumb

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_int32(1, self.width)
        msg.add_int32(2, self.height)
        msg.add_bytes(3, self.thumb)
        return msg.serialize()


class ImageDimensions:
    """Image/video dimensions { width(1), height(2) }."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_int32(1, self.width)
        msg.add_int32(2, self.height)
        return msg.serialize()


class ImageExt:
    """Image/video document extension { image(1) }."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_message(1, ImageDimensions(self.width, self.height))
        return msg.serialize()


class AudioMeta:
    """Audio metadata { duration(1), title(2), performer(3), genre(4), album(6) }."""

    def __init__(
        self,
        duration: int = 0,
        title: Optional[str] = None,
        performer: Optional[str] = None,
        genre: Optional[str] = None,
        album: Optional[str] = None,
    ):
        self.duration = duration
        self.title = title
        self.performer = performer
        self.genre = genre
        self.album = album

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_int32(1, self.duration)
        msg.add_string(2, self.title)
        msg.add_string(3, self.performer)
        msg.add_string(4, self.genre)
        msg.add_string(6, self.album)
        return msg.serialize()


class AudioExt:
    """Audio document extension { audio(1) }."""

    def __init__(self, duration: int = 0, title: Optional[str] = None, performer: Optional[str] = None):
        self.duration = duration
        self.title = title
        self.performer = performer

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_message(1, AudioMeta(self.duration, self.title, self.performer))
        return msg.serialize()


class DocumentMessage:
    """Document message for media attachments.

    Fields:
      1: fileId (int64)
      2: accessHash (int64)
      3: fileSize (int32)
      4: name (string)
      5: mimeType (string)
      6: thumb (FastThumb)
      7: ext (DocumentEx)
      8: caption (TextMessage)
    """

    def __init__(
        self,
        file_id: int,
        access_hash: int,
        file_size: int,
        name: str,
        mime_type: str,
        caption: Optional[str] = None,
        thumb: Optional[FastThumb] = None,
        ext: Optional[Union[ImageExt, AudioExt]] = None,
    ):
        self.file_id = file_id
        self.access_hash = access_hash
        self.file_size = file_size
        self.name = name
        self.mime_type = mime_type
        self.caption = caption
        self.thumb = thumb
        self.ext = ext

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_int64(1, self.file_id)
        msg.add_int64(2, self.access_hash)
        msg.add_int32(3, self.file_size)
        msg.add_string(4, self.name)
        msg.add_string(5, self.mime_type)
        if self.thumb is not None:
            msg.add_message(6, self.thumb)
        if self.ext is not None:
            msg.add_message(7, self.ext)
        if self.caption is not None:
            msg.add_message(8, TextMessage(self.caption))
        return msg.serialize()


class MetadataEntry:
    """Single metadata key-value pair."""

    def __init__(self, key: str, value: str):
        self.key = key
        self.value = value

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_string(1, self.key)
        # Field 2 is a StringValue wrapper { field 1: value }
        sv = ProtobufMessage()
        sv.add_string(1, self.value)
        msg.add_message(2, sv)
        return msg.serialize()


class Metadata:
    """Metadata container with repeated entries."""

    def __init__(self, entries: Optional[Dict[str, str]] = None):
        self.entries = entries or {}

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        for key, value in self.entries.items():
            msg.add_message(1, MetadataEntry(key, value))
        return msg.serialize()


class WsInnerWrapper:
    """Inner wrapper for WebSocket gRPC-Web frames."""

    def __init__(
        self,
        service: str,
        method: str,
        payload: bytes,
        metadata: Optional[Dict[str, str]] = None,
        flags: int = 25,
    ):
        self.service = service
        self.method = method
        self.payload = payload
        self.metadata = metadata or {}
        self.flags = flags

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_string(1, self.service)
        msg.add_string(2, self.method)
        msg.add_bytes(3, self.payload)
        msg.add_message(4, Metadata(self.metadata))
        msg.add_int32(5, self.flags)
        return msg.serialize()


class WsClientPack:
    """Outer wrapper sent over WebSocket."""

    def __init__(self, inner: WsInnerWrapper):
        self.inner = inner

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_bytes(1, self.inner.serialize())
        return msg.serialize()


class SendMessageRequest:
    """Request payload for bale.messaging.v2.Messaging/SendMessage"""

    def __init__(
        self,
        peer_id: int,
        text: Optional[str] = None,
        document: Optional[bytes] = None,
        random_id: Optional[int] = None,
        reply_to_message_id: Optional[int] = None,
        access_hash: Optional[int] = None,
    ):
        self.peer_id = peer_id
        self.text = text
        self.document = document
        # Use a large random int64 if not provided
        self.random_id = random_id or random.randint(1, 2**63 - 1)
        self.reply_to_message_id = reply_to_message_id
        self.access_hash = access_hash

    def serialize(self) -> bytes:
        # Use ExPeer when access_hash is available; required for non-contacts.
        if self.access_hash:
            peer = ExPeer(self.peer_id, access_hash=self.access_hash)
        else:
            peer = Peer(self.peer_id)
        msg = ProtobufMessage()
        msg.add_message(1, peer)
        msg.add_int64(2, self.random_id)
        msg.add_message(3, MessageContent(text=self.text, document=self.document))
        if self.reply_to_message_id is not None:
            # Field 4: replyTo peer (same type, different message_id as id)
            msg.add_message(4, Peer(self.reply_to_message_id))
        # Field 6 is a duplicate peer in captures
        msg.add_message(6, peer)
        return msg.serialize()


class UpdateMessageRequest:
    """Request payload for bale.messaging.v2.Messaging/UpdateMessage (edit)."""

    def __init__(
        self,
        peer_id: int,
        message_id: int,
        text: str,
    ):
        self.peer_id = peer_id
        self.message_id = message_id
        self.text = text

    def serialize(self) -> bytes:
        peer = Peer(self.peer_id)
        msg = ProtobufMessage()
        msg.add_message(1, peer)
        # message_id is typically the message to edit (rid field)
        msg.add_int64(2, self.message_id)
        msg.add_message(3, MessageContent(text=self.text))
        msg.add_message(6, peer)
        return msg.serialize()


class DeleteMessageRequest:
    """Request payload for bale.messaging.v2.Messaging/DeleteMessage."""

    def __init__(
        self,
        peer_id: int,
        message_ids: List[int],
    ):
        self.peer_id = peer_id
        self.message_ids = message_ids

    def serialize(self) -> bytes:
        peer = Peer(self.peer_id)
        msg = ProtobufMessage()
        msg.add_message(1, peer)
        # rids field is packed repeated int64
        msg.add_packed_int64(2, self.message_ids)
        msg.add_message(6, peer)
        return msg.serialize()


class MessageReadRequest:
    """Request payload for bale.messaging.v2.Messaging/MessageRead"""

    def __init__(self, peer_id: int, max_id: int):
        self.peer_id = peer_id
        self.max_id = max_id

    def serialize(self) -> bytes:
        peer = Peer(self.peer_id)
        msg = ProtobufMessage()
        msg.add_message(1, peer)
        msg.add_int64(2, self.max_id)
        msg.add_message(6, peer)
        return msg.serialize()


class StopTypingRequest:
    """Request payload for bale.messaging.v2.Messaging/StopTyping"""

    def __init__(self, peer_id: int):
        self.peer_id = peer_id

    def serialize(self) -> bytes:
        peer = Peer(self.peer_id)
        msg = ProtobufMessage()
        msg.add_message(1, peer)
        msg.add_message(6, peer)
        return msg.serialize()


# ------------------------------------------------------------------
# File service protobuf builders
# ------------------------------------------------------------------

class File:
    """File reference for download/upload.

    Fields:
      1: fileId (int64)
      2: accessHash (int64)
      3: fileStorageVersion (int32)
    """

    def __init__(self, file_id: int, access_hash: int, file_storage_version: int = 0):
        self.file_id = file_id
        self.access_hash = access_hash
        self.file_storage_version = file_storage_version

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_int64(1, self.file_id)
        msg.add_int64(2, self.access_hash)
        if self.file_storage_version:
            msg.add_int32(3, self.file_storage_version)
        return msg.serialize()


class GetNasimFileUrlRequest:
    """Request for ai.bale.server.Files/GetNasimFileUrl.

    The web client calls this as ``GetNasimFileUrl({file: fileLocation})``
    where ``fileLocation`` is ``{fileId, accessHash, fileStorageVersion}``.

    Fields:
      1: file (File) {fileId(1), accessHash(2), fileStorageVersion(3)}
    """

    def __init__(self, file_id: int, access_hash: int, file_storage_version: int = 0):
        self.file_id = file_id
        self.access_hash = access_hash
        self.file_storage_version = file_storage_version

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_message(1, File(self.file_id, self.access_hash, self.file_storage_version))
        return msg.serialize()


class GetNasimFileUrlsRequest:
    """Request for ai.bale.server.Files/GetNasimFileUrls.

    Fields:
      1: peer (Peer)
      2: files (repeated File)
    """

    def __init__(self, peer_id: int, files: List[Dict[str, int]]):
        self.peer_id = peer_id
        self.files = files

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_message(1, Peer(self.peer_id))
        for f in self.files:
            msg.add_message(2, File(f["file_id"], f["access_hash"], f.get("file_storage_version", 0)))
        return msg.serialize()


class StringValueWrapper:
    """google.protobuf.StringValue wrapper for field 3."""

    def __init__(self, value: str):
        self.value = value

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_string(1, self.value)
        return msg.serialize()


class GetNasimFileUploadUrlRequest:
    """Request for ai.bale.server.Files/GetNasimFileUploadUrl.

    Fields:
      1: expectedSize (int32)
      2: crc (int64)
      3: uid (int64)
      4: name (string)
      5: mimeType (string)
      6: exPeer (ExPeer)
      7: sendType (SendTypeValue)
      8: chunkSize (int64)
    """

    def __init__(
        self,
        expected_size: int,
        name: str,
        mime_type: str,
        uid: int,
        send_type: int,
        peer_type: int = Peer.PEER_TYPE_USER,
        access_hash: int = 0,
        crc: int = 0,
        chunk_size: int = 0,
    ):
        self.expected_size = expected_size
        self.crc = crc
        self.uid = uid
        self.name = name
        self.mime_type = mime_type
        self.peer_type = peer_type
        self.access_hash = access_hash
        self.send_type = send_type
        self.chunk_size = chunk_size

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_int32(1, self.expected_size)
        msg.add_int64(2, self.crc)
        msg.add_int64(3, self.uid)
        msg.add_string(4, self.name)
        msg.add_string(5, self.mime_type)
        msg.add_message(6, ExPeer(self.uid, self.peer_type, self.access_hash))
        msg.add_message(7, SendTypeValue(self.send_type))
        if self.chunk_size:
            msg.add_int64(8, self.chunk_size)
        return msg.serialize()


def build_ws_frame(
    service: str,
    method: str,
    payload: bytes,
    metadata: Optional[Dict[str, str]] = None,
) -> bytes:
    """Build a complete WebSocket frame for sending."""
    inner = WsInnerWrapper(
        service=service,
        method=method,
        payload=payload,
        metadata=metadata,
    )
    return WsClientPack(inner).serialize()


# ------------------------------------------------------------------
# Dialog / History / User loaders
# ------------------------------------------------------------------

class UserOutPeer:
    """UserOutPeer { uid(1), accessHash(2) } for LoadUsers."""

    def __init__(self, uid: int, access_hash: int = 0):
        self.uid = uid
        self.access_hash = access_hash

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_int64(1, self.uid)
        msg.add_int64(2, self.access_hash)
        return msg.serialize()


class GroupOutPeer:
    """GroupOutPeer { groupId(1), accessHash(2) } for LoadGroups."""

    def __init__(self, group_id: int, access_hash: int = 0):
        self.group_id = group_id
        self.access_hash = access_hash

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_int64(1, self.group_id)
        msg.add_int64(2, self.access_hash)
        return msg.serialize()


class LoadGroupsRequest:
    """Request for bale.groups.v1.Groups/LoadGroups.

    Fields:
      1: groupPeers (repeated GroupOutPeer)
    """

    def __init__(self, group_peers: List[Dict[str, int]]):
        self.group_peers = group_peers

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        for gp in self.group_peers:
            msg.add_message(1, GroupOutPeer(gp["group_id"], gp.get("access_hash", 0)))
        return msg.serialize()


class LoadDialogsRequest:
    """Request for bale.messaging.v2.Messaging/LoadDialogs.

    Fields:
      1: minDate (int64)
      2: limit (int32)
      3: optimizations (repeated int32)
      4: dialogType (int32)
      5: excludePinnedDialogs (bool)
      6: archiveFilter (int32)
    """

    def __init__(
        self,
        limit: int = 100,
        min_date: int = 0,
        dialog_type: int = 0,
        exclude_pinned: bool = False,
        archive_filter: int = 0,
        optimizations: Optional[List[int]] = None,
    ):
        self.limit = limit
        self.min_date = min_date
        self.dialog_type = dialog_type
        self.exclude_pinned = exclude_pinned
        self.archive_filter = archive_filter
        self.optimizations = optimizations

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_int64(1, self.min_date)
        msg.add_int32(2, self.limit)
        if self.optimizations is not None:
            for opt in self.optimizations:
                msg.add_int32(3, opt)
        msg.add_int32(4, self.dialog_type)
        msg.add_bool(5, self.exclude_pinned)
        msg.add_int32(6, self.archive_filter)
        return msg.serialize()


class LoadHistoryRequest:
    """Request for bale.messaging.v2.Messaging/LoadHistory.

    Fields:
      1: peer (Peer)
      2: date (int64)
      4: loadMode (int32)
      5: limit (int32)
      6: optimizations (repeated int32)
    """

    def __init__(
        self,
        peer_id: int,
        peer_type: int = Peer.PEER_TYPE_USER,
        date: int = 0,
        limit: int = 50,
        load_mode: int = 2,
    ):
        self.peer_id = peer_id
        self.peer_type = peer_type
        self.date = date
        self.limit = limit
        self.load_mode = load_mode

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_message(1, Peer(self.peer_id, self.peer_type))
        msg.add_int64(2, self.date)
        msg.add_int32(4, self.load_mode)
        msg.add_int32(5, self.limit)
        return msg.serialize()


class LoadUsersRequest:
    """Request for bale.messaging.v2.Messaging/LoadUsers.

    Fields:
      1: userPeers (repeated UserOutPeer)
    """

    def __init__(self, user_peers: List[Dict[str, int]]):
        self.user_peers = user_peers

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        for up in self.user_peers:
            msg.add_message(1, UserOutPeer(up["uid"], up.get("access_hash", 0)))
        return msg.serialize()


class SearchContactsRequest:
    """Request for bale.users.v1.Users/SearchContacts.

    Fields:
      1: request (string)
      2: optimizations (repeated int32)
    """

    def __init__(self, request: str, optimizations: Optional[List[int]] = None):
        self.request = request
        self.optimizations = optimizations or []

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_string(1, self.request)
        for opt in self.optimizations:
            msg.add_int32(2, opt)
        return msg.serialize()

class PhoneContact:
    """Single phone contact entry for ImportContacts."""

    def __init__(self, phone_number: int, name: Optional[str] = None):
        self.phone_number = phone_number
        self.name = name

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        if self.phone_number:
            msg.add_int64(1, self.phone_number)
        if self.name:
            msg.add_message(2, ProtobufMessage().add_string(1, self.name))
        return msg.serialize()


class ImportContactsRequest:
    """Request for bale.users.v1.Users/ImportContacts.

    Fields:
      1: phones (repeated PhoneContact)
      3: optimizations (repeated int32, packed)
    """

    def __init__(self, phones: List[PhoneContact], optimizations: Optional[List[int]] = None):
        self.phones = phones
        self.optimizations = optimizations or []

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        for phone in self.phones:
            msg.add_message(1, phone)
        if self.optimizations:
            msg.add_packed_int32(3, self.optimizations)
        return msg.serialize()

class GetContactsRequest:
    """Request for bale.users.v1.Users/GetContacts.

    Fields:
      1: contactsHash (string)
      2: optimizations (repeated int32, packed)
    """

    def __init__(self, contacts_hash: str = "", optimizations: Optional[List[int]] = None):
        self.contacts_hash = contacts_hash
        self.optimizations = optimizations or []

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_string(1, self.contacts_hash)
        if self.optimizations:
            msg.add_packed_int32(2, self.optimizations)
        return msg.serialize()
