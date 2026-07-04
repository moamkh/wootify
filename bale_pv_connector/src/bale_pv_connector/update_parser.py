"""
Bale Update Parser
==================

Parse incoming WebSocket update protobufs into readable events.

Reverse-engineered from web.bale.ai JS bundle and live captures.

WebSocket Update Frame Structure:
- Outer wrapper: {1: inner_bytes}
- Inner wrapper: {1: message_wrapper_bytes, 3: index, 4: timestamp}
- Message wrapper:
    19: messageStatus (read/delivery receipt)
    46: contactStatus (peer + auth key)
    50: readReceipt/status update
    55: updateMessage (new private/group message)
    131: appSettings (in_app_message_config, drafts, view counts)
    162: channelMessage (channel/broadcast message)
    52807-52810: callSignaling (voice/video call events)
- UpdateMessage (field 55):
    1: peer (Peer)
    2: senderUid (int32)
    3: date (int64)
    4: rid (int64)
    5: message (Message G)
    9: status/ref (bytes)
    14: peer info (bytes)
- ChannelMessage (field 162):
    1: peer (Peer)
    2: messageId (int64)
    3/8: message (Message G)
    4: sender info {1: senderUid}
    9: channelPeer (Peer)

Message (G):
    4: documentMessage (Document)
    12: stickerMessage (StickerMessage)
    15: textMessage (TextMessage)

StickerMessage (field 12 of Message G):
    1: stickerSet ref (bytes)
    3: large variant (512x512) {
        1: fileReference bytes { 1: fileId (int64), 2: accessHash (int64), 3: {1: fileStorageVersion} }
        2: width (int32)
        3: height (int32)
        4: fileSize (int32)
    }
    4: medium variant (256x256) { same structure }
    5: another pack reference (bytes)
    6: emoji / label (string)

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

logger = logging.getLogger("bale_pv_connector.updates")

# Known WebSocket message-wrapper field numbers observed in live captures.
# Field 55 is the classic UpdateMessage; other fields carry settings,
# receipts, channel posts, and call signaling.
class BaleUpdateType:
    """Symbolic names for wrapper-level update fields."""

    STATUS = 4
    HEARTBEAT = 5
    MESSAGE_STATUS = 19
    TYPING = 21
    CONTACT_STATUS = 46
    READ_RECEIPT = 50
    NEW_MESSAGE = 55
    APP_SETTINGS = 131
    CHANNEL_MESSAGE = 162
    # Seen in recent captures: call-signaling field numbers are not a tight block.
    CALL_SIGNALING_START = 52805
    CALL_SIGNALING_END = 52832


# Wrapper fields that the parser recognizes and should not warn about.
KNOWN_WRAPPER_FIELDS: set[int] = {
    BaleUpdateType.STATUS,
    BaleUpdateType.HEARTBEAT,
    BaleUpdateType.MESSAGE_STATUS,
    BaleUpdateType.TYPING,
    BaleUpdateType.CONTACT_STATUS,
    BaleUpdateType.READ_RECEIPT,
    BaleUpdateType.NEW_MESSAGE,
    BaleUpdateType.APP_SETTINGS,
    BaleUpdateType.CHANNEL_MESSAGE,
    *range(BaleUpdateType.CALL_SIGNALING_START, BaleUpdateType.CALL_SIGNALING_END + 1),
    # Additional observed fields that we don't parse yet but want to silence.
    23,
    24,
    36,
    40,
    48,
    85,
    721,
    722,
    2622,
    2627,
    54323,
    54335,
    54341,
}


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

        # Extract fileStorageVersion from field 11 (wrapper {1: int32}).
        # All observed live captures include "\b\x01" = {1: 1} here.
        # GetNasimFileUrl requires the correct version to resolve sticker URLs.
        fsv_bytes = fields.get(11, [None])[0]
        file_storage_version = 0
        if isinstance(fsv_bytes, bytes) and fsv_bytes:
            try:
                fsv_fields = ProtobufParser(fsv_bytes).parse()
                fsv = fsv_fields.get(1, [None])[0]
                if isinstance(fsv, int):
                    file_storage_version = fsv
            except Exception:
                pass

        result: Dict[str, Any] = {
            "file_id": str(file_id) if file_id is not None else None,
            "access_hash": str(access_hash) if access_hash is not None else None,
            "file_size": file_size,
            "file_name": name,
            "mime_type": mime_type,
            "caption": caption,
            "file_storage_version": file_storage_version,
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
        logger.warning("bale_ws_parse_document_message_failed error=%s data_len=%s", exc, len(data))
        return None


def _parse_sticker_message(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse StickerMessage (field 12 of Message G).

    Bale sends dedicated stickers via this field — distinct from DocumentMessage
    (field 4).  The structure contains one or more size variants; we pick the
    largest available (field 3 = 512x512, field 4 = 256x256).

    Each variant encodes a *fileReference* blob in its field 1:
      { 1: fileId (int64), 2: accessHash (int64), 3: {1: fileStorageVersion} }
    """
    try:
        fields = ProtobufParser(data).parse()

        # Prefer large variant (field 3), fall back to medium (field 4).
        for variant_field in (3, 4):
            variant_bytes = fields.get(variant_field, [None])[0]
            if not isinstance(variant_bytes, bytes):
                continue

            variant = ProtobufParser(variant_bytes).parse()
            file_ref_bytes = variant.get(1, [None])[0]
            width = variant.get(2, [None])[0]
            height = variant.get(3, [None])[0]
            file_size = variant.get(4, [None])[0]

            if not isinstance(file_ref_bytes, bytes):
                continue

            file_ref = ProtobufParser(file_ref_bytes).parse()
            file_id = file_ref.get(1, [None])[0]
            access_hash = file_ref.get(2, [None])[0]

            fsv_bytes = file_ref.get(3, [None])[0]
            file_storage_version = 0
            if isinstance(fsv_bytes, bytes) and fsv_bytes:
                try:
                    fsv_fields = ProtobufParser(fsv_bytes).parse()
                    fsv = fsv_fields.get(1, [None])[0]
                    if isinstance(fsv, int):
                        file_storage_version = fsv
                except Exception:
                    pass

            if file_id is None:
                continue

            return {
                "file_id": str(file_id),
                "access_hash": str(access_hash) if access_hash is not None else None,
                "file_size": file_size,
                # Use .webp extension so downstream sticker detection is unambiguous.
                "file_name": f"sticker{file_id}.webp",
                "mime_type": "image/webp",
                "caption": "",
                "file_storage_version": file_storage_version,
                "width": width,
                "height": height,
            }

        return None
    except Exception as exc:
        logger.warning("bale_ws_parse_sticker_message_failed error=%s data_len=%s", exc, len(data))
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

        # Field 12 = stickerMessage (dedicated sticker type — NOT a DocumentMessage).
        # Bale sends stickers via this field in both private and group chats.
        sticker_bytes = fields.get(12, [None])[0]
        if sticker_bytes and isinstance(sticker_bytes, bytes):
            sticker = _parse_sticker_message(sticker_bytes)
            if sticker:
                return {
                    "text": "",
                    "media": sticker,
                    "message_type": "document",
                }

        # Field 15 = textMessage
        text_bytes = fields.get(15, [None])[0]
        if text_bytes and isinstance(text_bytes, bytes):
            text = _parse_text_message(text_bytes)
            if text:
                return {"text": text, "message_type": "text"}
        logger.debug("bale_ws_parse_message_content_empty data_len=%s fields=%s", len(data), list(fields.keys()))
        return {}
    except Exception as exc:
        logger.warning("bale_ws_parse_message_content_failed error=%s data_len=%s", exc, len(data))
        return {}


def _parse_forward_header(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse UpdateMessage field 7 (forwarded message header).

    Observed layout:
      1: original chat peer bytes (possibly a channel/group)
      2: original sender peer bytes
      3: forward date / original message date
      4: original message id
      5: original Message(G) bytes (document/text/etc.)
      0: unknown flags
    """
    try:
        fields = ProtobufParser(data).parse()

        # Field 5 carries the forwarded Message(G) content. This is where the
        # actual attachment + caption live for forwarded media.
        msg_bytes = fields.get(5, [None])[0]
        message_content: Optional[Dict[str, Any]] = None
        if isinstance(msg_bytes, bytes) and msg_bytes:
            message_content = _parse_message_content(msg_bytes)

        # Try to extract original sender/chat peer info.
        # Field 2 normally carries the original sender; field 1 the original chat.
        from_peer: Optional[Dict[str, Any]] = None
        from_id: Optional[int] = None
        from_peer_bytes = fields.get(2, [None])[0]
        if isinstance(from_peer_bytes, bytes):
            from_peer = _parse_peer(from_peer_bytes)
            if from_peer and isinstance(from_peer.get("id"), int):
                from_id = from_peer["id"]
            else:
                parsed = ProtobufParser(from_peer_bytes).parse()
                val = parsed.get(1, [None])[0]
                if isinstance(val, int):
                    from_id = val
                    from_peer = {"id": val}
        if from_id is None:
            # Fallback to field 1 if it contains a single int id
            chat_peer_bytes = fields.get(1, [None])[0]
            if isinstance(chat_peer_bytes, bytes):
                parsed_chat = ProtobufParser(chat_peer_bytes).parse()
                chat_id = parsed_chat.get(1, [None])[0]
                if isinstance(chat_id, int):
                    from_id = chat_id

        result: Dict[str, Any] = {
            "from_id": from_id,
            "from_peer": from_peer,
            "forward_date": fields.get(3, [None])[0],
            "forward_message_id": fields.get(4, [None])[0],
            "message": message_content,
        }
        return result
    except Exception as exc:
        logger.debug("parse_forward_header failed: %s", exc)
        return None


def _log_unknown_wrapper_fields(wrapper: Dict[int, Any], raw_wrapper: bytes) -> None:
    """Log unknown wrapper field numbers for reverse-engineering."""
    unknown = [k for k in wrapper.keys() if k not in KNOWN_WRAPPER_FIELDS]
    if unknown:
        logger.warning(
            "bale_ws_unknown_wrapper_fields unknown=%s raw_len=%s raw_hex=%s",
            unknown,
            len(raw_wrapper),
            raw_wrapper[:64].hex(),
        )


def _parse_message_status_update(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse wrapper field 19 (read/delivery receipt).

    Structure:
      1: peer (Peer)
      2: date/timestamp (int64)
    """
    try:
        fields = ProtobufParser(data).parse()
        peer_bytes = fields.get(1, [None])[0]
        date = fields.get(2, [None])[0]
        peer = _parse_peer(peer_bytes) if isinstance(peer_bytes, bytes) else None
        result: Dict[str, Any] = {
            "type": "message_status",
            "peer": peer,
            "date": date,
        }
        return result
    except Exception as exc:
        logger.debug("parse_message_status_update failed: %s", exc)
        return None


def _parse_app_settings_update(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse wrapper field 131 (app settings / in-app messages / drafts).

    Structure:
      1: settings key (string), e.g. "in_app_message_config"
      2: payload (bytes)
    """
    try:
        fields = ProtobufParser(data).parse()
        key_bytes = fields.get(1, [None])[0]
        payload = fields.get(2, [None])[0]
        key = ""
        if isinstance(key_bytes, bytes):
            key = key_bytes.decode("utf-8", errors="replace")
        result: Dict[str, Any] = {
            "type": "app_settings",
            "settings_key": key,
            "settings_payload": payload if isinstance(payload, bytes) else None,
        }
        return result
    except Exception as exc:
        logger.debug("parse_app_settings_update failed: %s", exc)
        return None


def _parse_channel_message_update(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse wrapper field 162 (channel/broadcast message).

    Structure:
      1: peer (Peer) - usually type 2
      2: messageId (int64)
      3/8: message (Message G)
      4: senderInfo {1: senderUid}
      5: status/date? (varint)
      9: channelPeer (Peer) - usually type 3
    """
    try:
        fields = ProtobufParser(data).parse()
        peer_bytes = fields.get(1, [None])[0]
        message_id = fields.get(2, [None])[0]
        msg_bytes = fields.get(3, [None])[0] or fields.get(8, [None])[0]
        sender_info = fields.get(4, [None])[0]
        date = fields.get(5, [None])[0]
        channel_peer_bytes = fields.get(9, [None])[0]

        peer = _parse_peer(peer_bytes) if isinstance(peer_bytes, bytes) else None
        channel_peer = (
            _parse_peer(channel_peer_bytes) if isinstance(channel_peer_bytes, bytes) else None
        )

        sender_uid: Optional[int] = None
        if isinstance(sender_info, bytes):
            try:
                si = ProtobufParser(sender_info).parse()
                uid = si.get(1, [None])[0]
                if isinstance(uid, int):
                    sender_uid = uid
            except Exception:
                pass

        result: Dict[str, Any] = {
            "type": "channel_message",
            "sender_uid": sender_uid,
            "message_id": str(message_id) if message_id is not None else None,
            "rid": str(message_id) if message_id is not None else None,
            "date": date,
            "peer": peer,
            "channel_peer": channel_peer,
        }

        if msg_bytes and isinstance(msg_bytes, bytes):
            content = _parse_message_content(msg_bytes)
            if content:
                result.update(content)

        return result
    except Exception as exc:
        logger.debug("parse_channel_message_update failed: %s", exc)
        return None


def _parse_inner_status(inner: Dict[int, Any]) -> Optional[Dict[str, Any]]:
    """Parse inner-wrapper fields 4/5 status frames.

    Some server pushes omit the message wrapper and send:
      4: timestamp (int64)
      5: status bytes (peer + message references)

    These are typically read/delivery or presence notifications.
    """
    try:
        timestamp = inner.get(4, [None])[0]
        status_bytes = inner.get(5, [None])[0]
        if not isinstance(timestamp, int) or not isinstance(status_bytes, bytes):
            return None

        status = ProtobufParser(status_bytes).parse()
        peer_bytes = status.get(1, [None])[0]
        peer = _parse_peer(peer_bytes) if isinstance(peer_bytes, bytes) else None

        result: Dict[str, Any] = {
            "type": "inner_status",
            "date": timestamp,
            "peer": peer,
        }

        # If the status message carries a single integer reference, expose it.
        ref = status.get(2, [None])[0]
        if isinstance(ref, int):
            result["reference_id"] = str(ref)

        return result
    except Exception as exc:
        logger.debug("parse_inner_status failed: %s", exc)
        return None


def _parse_status_heartbeat_update(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse wrapper fields 4/5 (status/heartbeat) frames.

    Recent frames use:
      4: timestamp (int64)
      5: status bytes (peer + message references)
    """
    try:
        fields = ProtobufParser(data).parse()
        timestamp = fields.get(4, [None])[0]
        status_bytes = fields.get(5, [None])[0]
        result: Dict[str, Any] = {"type": "status", "date": timestamp}
        if isinstance(status_bytes, bytes):
            status = ProtobufParser(status_bytes).parse()
            peer_bytes = status.get(1, [None])[0]
            peer = _parse_peer(peer_bytes) if isinstance(peer_bytes, bytes) else None
            if peer:
                result["peer"] = peer
            ref = status.get(2, [None])[0] or status.get(6, [None])[0]
            if isinstance(ref, int):
                result["reference_id"] = str(ref)
        return result
    except Exception as exc:
        logger.debug("parse_status_heartbeat_update failed: %s", exc)
        return None


def _parse_contact_status_payload(payload: bytes) -> Optional[Dict[str, Any]]:
    """Parse a contact/session status payload {1: peer, 2: auth key, ...}."""
    try:
        inner = ProtobufParser(payload).parse()
        peer_bytes = inner.get(1, [None])[0]
        peer = _parse_peer(peer_bytes) if isinstance(peer_bytes, bytes) else None
        return {"peer": peer}
    except Exception:
        return None


def _parse_contact_status_update(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse wrapper field 4 or 46 contact/session status frames.

    Recent frames use wrapper field 46; older/live frames also use field 4.
    Both carry a payload {1: peer, 2: auth/int, ...}.
    """
    try:
        fields = ProtobufParser(data).parse()
        for key in (46, 4):
            payload = fields.get(key, [None])[0]
            if isinstance(payload, bytes):
                parsed = _parse_contact_status_payload(payload)
                if parsed:
                    parsed["type"] = "contact_status"
                    return parsed
        return None
    except Exception as exc:
        logger.debug("parse_contact_status_update failed: %s", exc)
        return None


def _parse_peer_info(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse UpdateMessage field 14 (peer info) looking for chat title/name.

    Observed structure varies; common fields include:
      1: title or name (string)
      2: peer id (int64)
      3: peer type (int32)
      4..n: unknown flags/access_hash/etc.
    We return any string fields we can decode so callers can pick the best title,
    plus any integer fields that may be the peer access_hash.
    """
    try:
        fields = ProtobufParser(data).parse()
        result: Dict[str, Any] = {"raw_fields": {k: v for k, v in fields.items()}}
        strings: List[str] = []
        ints: List[int] = []
        for k, vals in fields.items():
            for v in vals:
                if isinstance(v, bytes):
                    try:
                        s = v.decode("utf-8", errors="replace")
                        if s:
                            strings.append(s)
                    except Exception:
                        pass
                elif isinstance(v, int):
                    ints.append(v)
        if strings:
            result["strings"] = strings
            result["title"] = strings[0]
        # Field 2 is the peer id and field 3 the peer type when present.
        peer_id = fields.get(2, [None])[0]
        if isinstance(peer_id, int):
            result["peer_id"] = peer_id
        peer_type = fields.get(3, [None])[0]
        if isinstance(peer_type, int):
            result["peer_type"] = peer_type
        # The first non-id/type integer is a good candidate for access_hash.
        for v in ints:
            if v not in (peer_id, peer_type):
                result["access_hash"] = v
                break
        return result
    except Exception as exc:
        logger.debug("parse_peer_info failed: %s", exc)
        return None


def _parse_read_receipt_update(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse wrapper field 50 read-receipt frames.

    Structure:
      50: bytes {1: peer, 2..n: message references}
    """
    try:
        fields = ProtobufParser(data).parse()
        payload = fields.get(50, [None])[0]
        if not isinstance(payload, bytes):
            return None
        inner = ProtobufParser(payload).parse()
        peer_bytes = inner.get(1, [None])[0]
        peer = _parse_peer(peer_bytes) if isinstance(peer_bytes, bytes) else None
        result: Dict[str, Any] = {"type": "read_receipt", "peer": peer}
        # Collect any integer refs (common fields 2/4)
        refs = []
        for ref_field in (2, 4):
            for val in inner.get(ref_field, []):
                if isinstance(val, int):
                    refs.append(str(val))
        if refs:
            result["reference_ids"] = refs
        return result
    except Exception as exc:
        logger.debug("parse_read_receipt_update failed: %s", exc)
        return None


def parse_ws_update(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse a WebSocket update frame.

    The WebSocket frame structure is:
      Outer(1|2) -> Inner(1) -> Container(1) -> Wrapper -> Event

    Supported wrapper events:
      4:  status/contactStatus
      5:  heartbeat/status bytes
      19: messageStatus (read/delivery receipt)
      46: contactStatus (peer + auth key)
      50: readReceipt/status update
      55: updateMessage (new private/group message)
      131: appSettings (in_app_message_config, drafts, view counts)
      162: channelMessage (channel/broadcast message)
      52805-52832: callSignaling (voice/video call events)

    UpdateMessage (field 55) fields:
      1: peer (Peer)
      2: senderUid (int32)
      3: date (int64)
      4: rid (int64)
      5: message (Message G)
      9: status reference
      14: peer info
    """
    try:
        # Unwrap outer {1: bytes} or {2: bytes}
        # Older captures use outer field 1; recent live frames use outer field 2.
        outer = ProtobufParser(data).parse()
        inner_bytes = outer.get(1, [None])[0] or outer.get(2, [None])[0]
        if not inner_bytes or not isinstance(inner_bytes, bytes):
            # Log other outer fields for reverse-engineering
            unknown_outer = [k for k in outer.keys() if k not in (1, 2)]
            if unknown_outer:
                logger.warning(
                    "bale_ws_unknown_outer_fields fields=%s raw_len=%s raw_hex=%s",
                    unknown_outer,
                    len(data),
                    data[:64].hex(),
                )
            return None

        # Unwrap inner {1: wrapper_bytes, 3: index, 4: timestamp}
        inner = ProtobufParser(inner_bytes).parse()
        wrapper_bytes = inner.get(1, [b""])[0]
        if not wrapper_bytes or not isinstance(wrapper_bytes, bytes):
            # Some server pushes use inner fields 4 (timestamp) and 5 (status)
            # instead of wrapping a message. Try to parse them as a status
            # update before falling back to the generic unknown-fields warning.
            status = _parse_inner_status(inner)
            if status is not None:
                return status

            unknown_inner = [k for k in inner.keys() if k != 1]
            if unknown_inner:
                logger.warning(
                    "bale_ws_unknown_inner_fields fields=%s raw_len=%s raw_hex=%s",
                    unknown_inner,
                    len(inner_bytes),
                    inner_bytes[:64].hex(),
                )
            return None

        # Newer server frames wrap the event one level deeper:
        # Inner(1) is a container {1: event_wrapper_bytes, 2: ..., 3: ..., 4: timestamp}.
        # Older frames have Inner(1) directly as the event wrapper.
        maybe_container = ProtobufParser(wrapper_bytes).parse()
        container_ts = maybe_container.get(4, [None])[0]
        if (
            set(maybe_container.keys()).issubset({1, 2, 3, 4})
            and isinstance(maybe_container.get(1, [None])[0], bytes)
        ):
            event_wrapper_bytes = maybe_container[1][0]
        else:
            event_wrapper_bytes = wrapper_bytes
            container_ts = None

        # Unwrap wrapper and dispatch by event type.
        wrapper = ProtobufParser(event_wrapper_bytes).parse()
        _log_unknown_wrapper_fields(wrapper, event_wrapper_bytes)

        def _apply_ts(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if result is not None and container_ts is not None and result.get("date") is None:
                result["date"] = container_ts
            return result

        # Field 4/5: status / heartbeat / contact-session frames
        status_bytes = wrapper.get(BaleUpdateType.STATUS, [None])[0]
        heartbeat_bytes = wrapper.get(BaleUpdateType.HEARTBEAT, [None])[0]
        if heartbeat_bytes is not None or status_bytes is not None:
            if heartbeat_bytes is not None:
                return _apply_ts(_parse_status_heartbeat_update(event_wrapper_bytes))
            return _apply_ts(_parse_contact_status_update(event_wrapper_bytes))

        # Field 46: contactStatus (peer + auth key)
        contact_bytes = wrapper.get(BaleUpdateType.CONTACT_STATUS, [None])[0]
        if isinstance(contact_bytes, bytes):
            return _apply_ts(_parse_contact_status_update(event_wrapper_bytes))

        # Field 50: read receipts / status updates
        read_bytes = wrapper.get(BaleUpdateType.READ_RECEIPT, [None])[0]
        if isinstance(read_bytes, bytes):
            return _apply_ts(_parse_read_receipt_update(event_wrapper_bytes))

        # Field 55: classic UpdateMessage (private/group chat)
        update_bytes = wrapper.get(BaleUpdateType.NEW_MESSAGE, [None])[0]
        if isinstance(update_bytes, bytes):
            update = ProtobufParser(update_bytes).parse()
            peer_bytes = update.get(1, [None])[0]
            sender_uid = update.get(2, [None])[0]
            date = update.get(3, [None])[0]
            rid = update.get(4, [None])[0]
            msg_bytes = update.get(5, [None])[0]

            # Field 7 carries forwarded-message metadata. Parse it explicitly so
            # we can extract the original attachment/caption and avoid treating
            # it as a reply-to reference.
            forward_info: Optional[Dict[str, Any]] = None
            forward_bytes = update.get(7, [None])[0]
            if isinstance(forward_bytes, bytes) and forward_bytes:
                forward_info = _parse_forward_header(forward_bytes)

            # Field 13 appears to be a reply-to reference message:
            #   {1: reply_to_msg_id (int64), 2: access_hash or peer_id (int64)}
            reply_ref_bytes = update.get(13, [None])[0]

            # Field 14 carries peer info (chat/group title, access hash, etc.).
            peer_info_bytes = update.get(14, [None])[0]
            peer_info: Optional[Dict[str, Any]] = None
            if isinstance(peer_info_bytes, bytes) and peer_info_bytes:
                peer_info = _parse_peer_info(peer_info_bytes)

            # Log any unknown fields in UpdateMessage (could signal edits/deletes)
            known_update_fields = {1, 2, 3, 4, 5, 7, 9, 13, 14}
            unknown_update = [k for k in update.keys() if k not in known_update_fields]
            if unknown_update:
                logger.warning(
                    "bale_ws_unknown_updatemessage_fields fields=%s rid=%s sender=%s values=%s",
                    unknown_update,
                    rid,
                    sender_uid,
                    {k: update.get(k) for k in unknown_update},
                )

            peer = _parse_peer(peer_bytes) if isinstance(peer_bytes, bytes) else None

            result: Dict[str, Any] = {
                "type": "message",
                "sender_uid": sender_uid,
                "rid": str(rid) if rid is not None else None,
                "date": date,
                "peer": peer,
            }

            if peer_info:
                result["peer_info"] = peer_info

            if forward_info and forward_info.get("from_id") is not None:
                result["forward_from"] = {
                    "from_id": forward_info.get("from_id"),
                    "forward_message_id": forward_info.get("forward_message_id"),
                    "forward_date": forward_info.get("forward_date"),
                }

            # Field 9 often carries a peer reference (sender for groups/channels,
            # recipient for 1-on-1). It usually contains two integers:
            #   {1: peer_id_or_access_hash, 2: peer_id_or_access_hash}
            # The exact meaning varies by message direction, so we preserve both
            # values and let the consumer pick the correct access_hash.
            sender_peer_bytes = update.get(9, [None])[0]
            if isinstance(sender_peer_bytes, bytes):
                try:
                    sender_peer = ProtobufParser(sender_peer_bytes).parse()
                    sp_f1 = sender_peer.get(1, [None])[0]
                    sp_f2 = sender_peer.get(2, [None])[0]
                    if isinstance(sp_f1, int):
                        result["sender_peer_field1"] = sp_f1
                    if isinstance(sp_f2, int):
                        result["sender_peer_field2"] = sp_f2
                except Exception:
                    pass

            # Try to extract reply-to message reference from undocumented fields
            # (common candidates: 6, 7, 8 in UpdateMessage protobuf). Skip field 7
            # when it has already been identified as a forward header.
            reply_to_msg_id: Optional[int] = None
            reply_candidates = [6, 8]
            if forward_info is None:
                reply_candidates.append(7)
            for candidate_field in reply_candidates:
                candidate_bytes = update.get(candidate_field, [None])[0]
                if isinstance(candidate_bytes, bytes) and len(candidate_bytes) >= 2:
                    try:
                        candidate = ProtobufParser(candidate_bytes).parse()
                        # Look for an integer field (likely message_id)
                        for msg_id_field in (1, 2, 4):
                            val = candidate.get(msg_id_field, [None])[0]
                            if isinstance(val, int) and val > 0:
                                reply_to_msg_id = val
                                logger.debug(
                                    "parse_ws_update found reply_to candidate_field=%s msg_id_field=%s msg_id=%s",
                                    candidate_field,
                                    msg_id_field,
                                    val,
                                )
                                break
                        if reply_to_msg_id is not None:
                            break
                    except Exception:
                        pass
            if reply_to_msg_id is not None:
                result["reply_to_msg_id"] = reply_to_msg_id

            # Field 13 is the authoritative reply-to reference when present.
            if isinstance(reply_ref_bytes, bytes) and reply_ref_bytes:
                try:
                    reply_ref = ProtobufParser(reply_ref_bytes).parse()
                    ref_msg_id = reply_ref.get(1, [None])[0]
                    if isinstance(ref_msg_id, int) and ref_msg_id > 0:
                        result["reply_to_msg_id"] = ref_msg_id
                        logger.debug(
                            "parse_ws_update found reply_to_field_13 msg_id=%s",
                            ref_msg_id,
                        )
                except Exception:
                    pass

            if msg_bytes and isinstance(msg_bytes, bytes):
                content = _parse_message_content(msg_bytes)
                if content:
                    result.update(content)

            # Forwarded messages often have an empty field 5; the real content
            # (attachment + caption) is nested inside the forward header.
            if forward_info and forward_info.get("message"):
                forwarded_content = forward_info["message"]
                if not result.get("text"):
                    result["text"] = forwarded_content.get("text", "")
                if not result.get("media") and forwarded_content.get("media"):
                    result["media"] = forwarded_content["media"]
                    result["message_type"] = forwarded_content.get("message_type", "document")

            return _apply_ts(result)

        # Field 162: channel/broadcast message
        channel_bytes = wrapper.get(BaleUpdateType.CHANNEL_MESSAGE, [None])[0]
        if isinstance(channel_bytes, bytes):
            return _apply_ts(_parse_channel_message_update(channel_bytes))

        # Field 19: read/delivery receipt
        status_bytes = wrapper.get(BaleUpdateType.MESSAGE_STATUS, [None])[0]
        if isinstance(status_bytes, bytes):
            return _apply_ts(_parse_message_status_update(status_bytes))

        # Field 131: app settings / in-app messages
        settings_bytes = wrapper.get(BaleUpdateType.APP_SETTINGS, [None])[0]
        if isinstance(settings_bytes, bytes):
            return _apply_ts(_parse_app_settings_update(settings_bytes))

        # Known but not-yet-parsed event types (call signaling, typing, etc.)
        known_unhandled = [
            k
            for k in wrapper.keys()
            if k in KNOWN_WRAPPER_FIELDS and k != BaleUpdateType.NEW_MESSAGE
        ]
        if known_unhandled:
            logger.debug("bale_ws_known_unhandled_event fields=%s", known_unhandled)
            return _apply_ts(
                {
                    "type": "known_unhandled",
                    "unhandled_fields": sorted(known_unhandled),
                }
            )

        return None
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
