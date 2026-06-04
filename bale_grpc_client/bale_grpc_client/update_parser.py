"""
Bale Update Parser
==================

Parse incoming WebSocket update protobufs into readable events.

Reverse-engineered from web.bale.ai JS bundle and live captures.

WebSocket Update Frame Structure:
- Outer wrapper: {1: inner_bytes}
- Inner wrapper: {1: message_wrapper_bytes, 3: index, 4: timestamp}
- Message wrapper: {55: update_message_bytes} (for new messages)
- UpdateMessage (field 55):
    1: peer (Peer)
    2: senderUid (int32)
    3: date (int64)
    4: rid (int64)
    5: message (Message G)
    9: status/ref (bytes)
    14: peer info (bytes)

Message (G):
    4: documentMessage (Document)
    15: textMessage (TextMessage)

DocumentMessage:
    1: fileId (int64)
    2: accessHash (int64)
    3: fileSize (int32)
    4: name (string)
    5: mimeType (string)
    6: thumb (ThumbMessage)
    7: ext (ExtMessage)
    8: caption (TextMessage)
    9: checkSum (wrapper)
    10: algorithm (wrapper)
    11: fileStorageVersion (wrapper)

ThumbMessage:
    1: width (int32)
    2: height (int32)
    3: data (bytes) - WEBP thumbnail

ExtMessage (image):
    1: width (int32)
    2: height (int32)

ExtMessage (audio):
    5: metadata (AudioMetadata)

AudioMetadata:
    1: duration_ms? (varint)
    2: title (string)
    3: artist (string)
    4: genre (string)
    6: album? (string)
"""

import logging
from typing import Any, Dict, List, Optional

from .protobuf_wire import ProtobufParser

logger = logging.getLogger("bale_grpc_client.updates")


def _parse_peer(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse a Peer message { type: int32, id: int64 }."""
    try:
        fields = ProtobufParser(data).parse()
        return {
            "type": fields.get(1, [None])[0],
            "id": fields.get(2, [None])[0],
        }
    except Exception:
        return None


def _parse_text_message(data: bytes) -> Optional[str]:
    """Parse TextMessage { text(1), ext(2) } and return the text."""
    try:
        fields = ProtobufParser(data).parse()
        text_val = fields.get(1, [b""])[0]
        if isinstance(text_val, bytes):
            return text_val.decode("utf-8", errors="replace")
        return str(text_val) if text_val else None
    except Exception:
        return None


def _parse_audio_ext(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse audio metadata from ext field."""
    try:
        fields = ProtobufParser(data).parse()
        result: Dict[str, Any] = {}
        # field 5 contains the metadata message
        meta_bytes = fields.get(5, [None])[0]
        if meta_bytes and isinstance(meta_bytes, bytes):
            meta = ProtobufParser(meta_bytes).parse()
            # field 1 = duration or bitrate (varint)
            dur = meta.get(1, [None])[0]
            if dur is not None:
                result["duration"] = dur
            # field 2 = title
            title = meta.get(2, [None])[0]
            if isinstance(title, bytes):
                result["title"] = title.decode("utf-8", errors="replace")
            # field 3 = artist/performer
            artist = meta.get(3, [None])[0]
            if isinstance(artist, bytes):
                result["performer"] = artist.decode("utf-8", errors="replace")
            # field 4 = genre
            genre = meta.get(4, [None])[0]
            if isinstance(genre, bytes):
                result["genre"] = genre.decode("utf-8", errors="replace")
            # field 6 = album
            album = meta.get(6, [None])[0]
            if isinstance(album, bytes):
                result["album"] = album.decode("utf-8", errors="replace")
        return result
    except Exception as exc:
        logger.debug("parse_audio_ext failed: %s", exc)
        return None


def _parse_image_ext(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse image dimensions from ext field."""
    try:
        fields = ProtobufParser(data).parse()
        result: Dict[str, Any] = {}
        # field 1 contains {1: width, 2: height}
        dim_bytes = fields.get(1, [None])[0]
        if dim_bytes and isinstance(dim_bytes, bytes):
            dims = ProtobufParser(dim_bytes).parse()
            w = dims.get(1, [None])[0]
            h = dims.get(2, [None])[0]
            if w is not None:
                result["width"] = w
            if h is not None:
                result["height"] = h
        return result
    except Exception as exc:
        logger.debug("parse_image_ext failed: %s", exc)
        return None


def _parse_document_message(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse DocumentMessage { fileId(1), accessHash(2), fileSize(3), name(4), mimeType(5), thumb(6), ext(7), caption(8) }.

    Returns dict with media metadata for download.
    """
    try:
        fields = ProtobufParser(data).parse()
        file_id = fields.get(1, [None])[0]
        access_hash = fields.get(2, [None])[0]
        file_size = fields.get(3, [None])[0]
        name_bytes = fields.get(4, [None])[0]
        mime_bytes = fields.get(5, [None])[0]
        thumb_bytes = fields.get(6, [None])[0]
        ext_bytes = fields.get(7, [None])[0]
        caption_bytes = fields.get(8, [None])[0]

        name = ""
        mime_type = ""
        caption = ""
        if isinstance(name_bytes, bytes):
            name = name_bytes.decode("utf-8", errors="replace")
        if isinstance(mime_bytes, bytes):
            mime_type = mime_bytes.decode("utf-8", errors="replace")
        if isinstance(caption_bytes, bytes):
            caption = _parse_text_message(caption_bytes) or ""

        result: Dict[str, Any] = {
            "file_id": str(file_id) if file_id is not None else None,
            "access_hash": str(access_hash) if access_hash is not None else None,
            "file_size": file_size,
            "file_name": name,
            "mime_type": mime_type,
            "caption": caption,
        }

        # Parse thumb for images/videos
        if thumb_bytes and isinstance(thumb_bytes, bytes):
            try:
                thumb_fields = ProtobufParser(thumb_bytes).parse()
                w = thumb_fields.get(1, [None])[0]
                h = thumb_fields.get(2, [None])[0]
                thumb_data = thumb_fields.get(3, [None])[0]
                if w is not None:
                    result["width"] = w
                if h is not None:
                    result["height"] = h
                if thumb_data and isinstance(thumb_data, bytes):
                    result["thumb_data"] = thumb_data
            except Exception:
                pass

        # Parse ext for additional metadata
        if ext_bytes and isinstance(ext_bytes, bytes):
            if mime_type.startswith("image/") or mime_type.startswith("video/"):
                dims = _parse_image_ext(ext_bytes)
                if dims:
                    result.update(dims)
            elif mime_type.startswith("audio/"):
                meta = _parse_audio_ext(ext_bytes)
                if meta:
                    result.update(meta)

        return result
    except Exception as exc:
        logger.debug("parse_document_message failed: %s", exc)
        return None


def _parse_message_content(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse Message (G) and extract text or media info."""
    try:
        fields = ProtobufParser(data).parse()

        # Field 4 = documentMessage (media: photo, video, audio, doc, etc.)
        doc_bytes = fields.get(4, [None])[0]
        if doc_bytes and isinstance(doc_bytes, bytes):
            doc = _parse_document_message(doc_bytes)
            if doc:
                result: Dict[str, Any] = {
                    "text": doc.get("caption") or "",
                    "media": doc,
                    "message_type": "document",
                }
                return result

        # Field 15 = textMessage
        text_bytes = fields.get(15, [None])[0]
        if text_bytes and isinstance(text_bytes, bytes):
            text = _parse_text_message(text_bytes)
            if text:
                return {"text": text, "message_type": "text"}
        return {}
    except Exception:
        return {}


def parse_ws_update(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse a WebSocket update frame.

    The WebSocket frame structure is:
      Outer(1) -> Inner(1) -> Wrapper(55) -> UpdateMessage

    UpdateMessage fields:
      1: peer (Peer)
      2: senderUid (int32)
      3: date (int64)
      4: rid (int64)
      5: message (Message G)
      9: status reference
      14: peer info
    """
    try:
        # Unwrap outer {1: bytes}
        outer = ProtobufParser(data).parse()
        inner_bytes = outer.get(1, [b""])[0]
        if not inner_bytes or not isinstance(inner_bytes, bytes):
            return None

        # Unwrap inner {1: wrapper_bytes, 3: index, 4: timestamp}
        inner = ProtobufParser(inner_bytes).parse()
        wrapper_bytes = inner.get(1, [b""])[0]
        if not wrapper_bytes or not isinstance(wrapper_bytes, bytes):
            return None

        # Unwrap wrapper {55: update_message_bytes} (or 50 for read receipts)
        wrapper = ProtobufParser(wrapper_bytes).parse()
        update_bytes = wrapper.get(55, [None])[0]
        if not update_bytes or not isinstance(update_bytes, bytes):
            return None

        # Parse UpdateMessage
        update = ProtobufParser(update_bytes).parse()
        peer_bytes = update.get(1, [None])[0]
        sender_uid = update.get(2, [None])[0]
        date = update.get(3, [None])[0]
        rid = update.get(4, [None])[0]
        msg_bytes = update.get(5, [None])[0]

        peer = _parse_peer(peer_bytes) if isinstance(peer_bytes, bytes) else None

        result: Dict[str, Any] = {
            "sender_uid": sender_uid,
            "rid": str(rid) if rid is not None else None,
            "date": date,
            "peer": peer,
        }

        if msg_bytes and isinstance(msg_bytes, bytes):
            content = _parse_message_content(msg_bytes)
            if content:
                result.update(content)

        return result
    except Exception as exc:
        logger.debug("parse_ws_update failed: %s", exc)
        return None


def parse_update_message(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse an UpdateMessage (n5) structure.

    DEPRECATED: This was based on an incorrect assumption about the
    WebSocket frame format. Use parse_ws_update() for WebSocket frames.
    Kept for backward compatibility with HTTP response parsing.

    Fields (for direct message bytes, not wrapped):
      1: senderUid (int32)
      2: rid (int64)
      3: date (int64)
      4: message (Message G)
      5: state (int32)
    """
    try:
        fields = ProtobufParser(data).parse()
        sender_uid = fields.get(1, [None])[0]
        rid = fields.get(2, [None])[0]
        date = fields.get(3, [None])[0]
        msg_bytes = fields.get(4, [None])[0]

        result: Dict[str, Any] = {
            "sender_uid": sender_uid,
            "rid": str(rid) if rid is not None else None,
            "date": date,
        }

        if msg_bytes and isinstance(msg_bytes, bytes):
            content = _parse_message_content(msg_bytes)
            if content:
                result.update(content)

        return result
    except Exception as exc:
        logger.debug("parse_update_message failed: %s", exc)
        return None


def parse_dialog(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse a Dialog (ia) structure.

    Fields:
      1: peer (Peer)
      2: unreadCount (int32)
      3: sortDate (int64)
      4: senderUid (int32)
      5: rid (int64)
      6: date (int64)
      7: message (Message G)
    """
    try:
        fields = ProtobufParser(data).parse()
        peer_bytes = fields.get(1, [None])[0]
        unread = fields.get(2, [0])[0]
        sort_date = fields.get(3, [None])[0]
        sender_uid = fields.get(4, [None])[0]
        rid = fields.get(5, [None])[0]
        date = fields.get(6, [None])[0]
        msg_bytes = fields.get(7, [None])[0]

        result: Dict[str, Any] = {
            "peer": _parse_peer(peer_bytes) if isinstance(peer_bytes, bytes) else None,
            "unread_count": unread,
            "sort_date": sort_date,
            "sender_uid": sender_uid,
            "rid": str(rid) if rid is not None else None,
            "date": date,
        }

        if msg_bytes and isinstance(msg_bytes, bytes):
            content = _parse_message_content(msg_bytes)
            if content:
                result.update(content)

        return result
    except Exception as exc:
        logger.debug("parse_dialog failed: %s", exc)
        return None
