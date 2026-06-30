"""
Bale Dialog / History / User Response Parsers
==============================================

Parse raw protobuf responses from:
- bale.messaging.v2.Messaging/LoadDialogs
- bale.messaging.v2.Messaging/LoadHistory
- bale.messaging.v2.Messaging/LoadUsers

Reverse-engineered from balethon-generated protobuf schemas.
"""

import logging
from typing import Any, Dict, List, Optional

from .protobuf_wire import ProtobufParser

logger = logging.getLogger("bale_pv_connector.dialogs")


def _decode_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return str(value)


def _parse_bool_value(data: bytes) -> Optional[bool]:
    """Parse google.protobuf.BoolValue { value(1): bool }."""
    try:
        fields = ProtobufParser(data).parse()
        val = fields.get(1, [None])[0]
        if val is None:
            return None
        return bool(val)
    except Exception:
        return None


def _parse_string_value(data: bytes) -> Optional[str]:
    """Parse google.protobuf.StringValue { value(1): string }."""
    try:
        fields = ProtobufParser(data).parse()
        val = fields.get(1, [None])[0]
        return _decode_str(val)
    except Exception:
        return None


def _parse_int64_value(data: bytes) -> Optional[int]:
    """Parse google.protobuf.Int64Value { value(1): int64 }."""
    try:
        fields = ProtobufParser(data).parse()
        return fields.get(1, [None])[0]
    except Exception:
        return None


def _parse_avatar(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse Avatar message (best-effort).

    Common observed layout:
      1: id/photo_id (int64)
      2: access_hash (int64)
      3: volume_id (int64)
      4: local_id (int32)
      5: dc_id (int32)
    The exact field numbers may vary; log raw bytes for unknown layouts.
    """
    try:
        fields = ProtobufParser(data).parse()
    except Exception as exc:
        logger.debug("parse_avatar parse_failed: %s", exc)
        return None

    # Collect all int64 candidates; commonly photo_id is field 1 and
    # access_hash is field 2.
    candidates: Dict[str, Any] = {}
    for field_num in (1, 2, 3, 4, 5):
        vals = fields.get(field_num)
        if vals:
            val = vals[0]
            if isinstance(val, int):
                candidates[f"field_{field_num}"] = val
            elif isinstance(val, bytes):
                candidates[f"field_{field_num}_hex"] = val.hex()

    if not candidates:
        logger.debug("parse_avatar unknown_layout hex=%s", data.hex())
        return None

    # Best-effort semantic mapping based on observed Bale schemas.
    result: Dict[str, Any] = {"raw": data.hex()}
    if 1 in fields:
        result["photo_id"] = fields[1][0]
    if 2 in fields:
        result["access_hash"] = fields[2][0]
    if 3 in fields:
        result["volume_id"] = fields[3][0]
    if 4 in fields:
        result["local_id"] = fields[4][0]
    if 5 in fields:
        result["dc_id"] = fields[5][0]
    return result


def parse_peer(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse Peer { type(1): int32, id(2): int64, access_hash(3): int64 }."""
    try:
        fields = ProtobufParser(data).parse()
        result: Dict[str, Any] = {
            "type": fields.get(1, [None])[0],
            "id": fields.get(2, [None])[0],
        }
        access_hash = fields.get(3, [None])[0]
        if isinstance(access_hash, int):
            result["access_hash"] = access_hash
        return result
    except Exception:
        return None


def parse_user(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse User message.

    Fields:
      1: id (int32)
      2: access_hash (int64)
      3: name (string)
      4: local_name (StringValue)
      5: sex (enum)
      6: avatar (Avatar)
      7: is_bot (BoolValue)
      9: nick (StringValue)
      16: is_deleted (BoolValue)
      19: created_at (Int64Value)
      20: ex_info (ExInfo)
      21: bot_ex_info (BotExInfo)
    """
    try:
        fields = ProtobufParser(data).parse()
        result = {
            "id": fields.get(1, [None])[0],
            "access_hash": fields.get(2, [None])[0],
            "name": _decode_str(fields.get(3, [None])[0]),
            "local_name": _parse_string_value(fields.get(4, [None])[0]) if 4 in fields else None,
            "sex": fields.get(5, [None])[0],
            "is_bot": _parse_bool_value(fields.get(7, [None])[0]) if 7 in fields else None,
            "nick": _parse_string_value(fields.get(9, [None])[0]) if 9 in fields else None,
            "is_deleted": _parse_bool_value(fields.get(16, [None])[0]) if 16 in fields else None,
            "created_at": _parse_int64_value(fields.get(19, [None])[0]) if 19 in fields else None,
        }
        if 6 in fields:
            avatar = _parse_avatar(fields[6][0])
            if avatar:
                result["avatar"] = avatar
        return result
    except Exception as exc:
        logger.debug("parse_user failed: %s", exc)
        return None


def parse_group(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse Group message from bale.groups.v1.Groups/LoadGroups responses.

    Observed LoadGroups layout:
      1: id (int32)
      2: access_hash (int64)
      17: groupInfo { 1: title (string) }
      20: members_count (int32)
      33: available_reactions (repeated string)

    Some older responses put the title directly in field 3, so we fall back
    to that when field 17 is absent.
    """
    try:
        fields = ProtobufParser(data).parse()
        title: Optional[str] = None
        info_bytes = fields.get(17, [None])[0]
        if isinstance(info_bytes, bytes):
            try:
                info = ProtobufParser(info_bytes).parse()
                title = _decode_str(info.get(1, [None])[0])
            except Exception:
                pass
        if not title:
            title = _decode_str(fields.get(3, [None])[0])
        return {
            "id": fields.get(1, [None])[0],
            "access_hash": fields.get(2, [None])[0],
            "title": title,
            "members_count": fields.get(20, [None])[0],
        }
    except Exception as exc:
        logger.debug("parse_group failed: %s", exc)
        return None


def parse_dialog(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse Dialog message.

    Fields:
      1: peer (Peer)
      2: unread_count (int32)
      3: sort_date (int64)
      4: sender_uid (int32)
      5: rid (int64)
      6: date (int64)
      7: message (Message)
      8: state (int32)
      9: first_unread_date (Int64Value)
      13: ex_info (ExInfo)
      14: is_message_forwarded (bool)
      17: marked_as_unread (bool)
      18: is_mute (bool)
    """
    try:
        fields = ProtobufParser(data).parse()
        peer_raw = fields.get(1, [None])[0]
        return {
            "peer": parse_peer(peer_raw) if peer_raw and isinstance(peer_raw, bytes) else None,
            "unread_count": fields.get(2, [None])[0],
            "sort_date": fields.get(3, [None])[0],
            "sender_uid": fields.get(4, [None])[0],
            "rid": fields.get(5, [None])[0],
            "date": fields.get(6, [None])[0],
            "state": fields.get(8, [None])[0],
            "is_message_forwarded": fields.get(14, [None])[0],
            "marked_as_unread": fields.get(17, [None])[0],
            "is_mute": fields.get(18, [None])[0],
        }
    except Exception as exc:
        logger.debug("parse_dialog failed: %s", exc)
        return None


def parse_load_groups_response(data: bytes) -> Dict[str, Any]:
    """Parse bale.groups.v1.Groups/LoadGroups response.

    Response body layout:
      1: groups (repeated Group)
    """
    try:
        fields = ProtobufParser(data).parse()
    except Exception as exc:
        logger.debug("parse_load_groups_response failed: %s", exc)
        return {"groups": []}

    groups: List[Dict[str, Any]] = []
    for raw in fields.get(1, []):
        if not isinstance(raw, bytes):
            continue
        parsed = parse_group(raw)
        if parsed:
            groups.append(parsed)

    return {"groups": groups}


def parse_load_dialogs_response(data: bytes) -> Dict[str, Any]:
    """Parse LoadDialogs response.

    Response fields:
      1: groups (repeated Group)
      2: users (repeated User)
      3: dialogs (repeated Dialog)
      4: user_peers (repeated UserOutPeer)
      5: group_peers (repeated GroupOutPeer)
    """
    result: Dict[str, Any] = {
        "groups": [],
        "users": [],
        "dialogs": [],
        "user_peers": [],
        "group_peers": [],
    }
    try:
        fields = ProtobufParser(data).parse()
        for raw in fields.get(1, []):
            parsed = parse_group(raw) if isinstance(raw, bytes) else None
            if parsed:
                result["groups"].append(parsed)
        for raw in fields.get(2, []):
            parsed = parse_user(raw) if isinstance(raw, bytes) else None
            if parsed:
                result["users"].append(parsed)
        for raw in fields.get(3, []):
            parsed = parse_dialog(raw) if isinstance(raw, bytes) else None
            if parsed:
                result["dialogs"].append(parsed)
        for raw in fields.get(4, []):
            peer = parse_peer(raw) if isinstance(raw, bytes) else None
            if peer:
                result["user_peers"].append(peer)
        for raw in fields.get(5, []):
            peer = parse_peer(raw) if isinstance(raw, bytes) else None
            if peer:
                result["group_peers"].append(peer)
    except Exception as exc:
        logger.warning("parse_load_dialogs_response failed: %s", exc)
    return result


def parse_load_users_response(data: bytes) -> Dict[str, Any]:
    """Parse LoadUsers response.

    Response fields:
      1: users (repeated User)
    """
    result: Dict[str, Any] = {"users": []}
    try:
        fields = ProtobufParser(data).parse()
        for raw in fields.get(1, []):
            parsed = parse_user(raw) if isinstance(raw, bytes) else None
            if parsed:
                result["users"].append(parsed)
    except Exception as exc:
        logger.warning("parse_load_users_response failed: %s", exc)
    return result


def parse_text_message(data: bytes) -> Optional[str]:
    """Parse TextMessage { text(1): string }."""
    try:
        fields = ProtobufParser(data).parse()
        return _decode_str(fields.get(1, [None])[0])
    except Exception:
        return None


def parse_document_message(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse DocumentMessage.

    Fields:
      1: file_id (int64)
      2: access_hash (int64)
      3: file_size (int32)
      4: name (string)
      5: mime_type (string)
      8: caption (TextMessage)
    """
    try:
        fields = ProtobufParser(data).parse()
        caption_raw = fields.get(8, [None])[0]
        return {
            "file_id": fields.get(1, [None])[0],
            "access_hash": fields.get(2, [None])[0],
            "file_size": fields.get(3, [None])[0],
            "name": _decode_str(fields.get(4, [None])[0]),
            "mime_type": _decode_str(fields.get(5, [None])[0]),
            "caption": parse_text_message(caption_raw) if caption_raw and isinstance(caption_raw, bytes) else None,
        }
    except Exception as exc:
        logger.debug("parse_document_message failed: %s", exc)
        return None


def parse_message_content(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse Message { documentMessage(4), textMessage(15) }."""
    try:
        fields = ProtobufParser(data).parse()
        text_raw = fields.get(15, [None])[0]
        if text_raw and isinstance(text_raw, bytes):
            return {"type": "text", "text": parse_text_message(text_raw)}
        doc_raw = fields.get(4, [None])[0]
        if doc_raw and isinstance(doc_raw, bytes):
            doc = parse_document_message(doc_raw)
            if doc:
                return {"type": "document", **doc}
        return None
    except Exception as exc:
        logger.debug("parse_message_content failed: %s", exc)
        return None


def parse_message_container(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse MessageContainer.

    Fields:
      1: sender_uid (int32)
      2: rid (int64)
      3: date (int64)
      4: message (Message)
      5: state (int32)
      7: attribute (MessageAttributes)
      8: quoted_message (QuotedMessage)
      12: edited_at (Int64Value)
      13: editor_user_id (Int32Value)
    """
    try:
        fields = ProtobufParser(data).parse()
        message_raw = fields.get(4, [None])[0]
        edited_at_raw = fields.get(12, [None])[0]
        editor_raw = fields.get(13, [None])[0]
        return {
            "sender_uid": fields.get(1, [None])[0],
            "rid": fields.get(2, [None])[0],
            "date": fields.get(3, [None])[0],
            "state": fields.get(5, [None])[0],
            "message": parse_message_content(message_raw) if message_raw and isinstance(message_raw, bytes) else None,
            "edited_at": _parse_int64_value(edited_at_raw) if edited_at_raw and isinstance(edited_at_raw, bytes) else None,
            "editor_user_id": _parse_int64_value(editor_raw) if editor_raw and isinstance(editor_raw, bytes) else None,
        }
    except Exception as exc:
        logger.debug("parse_message_container failed: %s", exc)
        return None


def parse_load_history_response(data: bytes) -> Dict[str, Any]:
    """Parse LoadHistory response.

    Response fields:
      1: history (repeated MessageContainer)
      2: users (repeated User)
      3: groups (repeated Group)
    """
    result: Dict[str, Any] = {"history": [], "users": [], "groups": []}
    try:
        fields = ProtobufParser(data).parse()
        for raw in fields.get(1, []):
            parsed = parse_message_container(raw) if isinstance(raw, bytes) else None
            if parsed:
                result["history"].append(parsed)
        for raw in fields.get(2, []):
            parsed = parse_user(raw) if isinstance(raw, bytes) else None
            if parsed:
                result["users"].append(parsed)
        for raw in fields.get(3, []):
            parsed = parse_group(raw) if isinstance(raw, bytes) else None
            if parsed:
                result["groups"].append(parsed)
        unknown = [k for k in fields.keys() if k not in {1, 2, 3}]
        if unknown:
            logger.debug("parse_load_history_response unknown_fields=%s", unknown)
    except Exception as exc:
        logger.warning("parse_load_history_response failed: %s", exc)
    return result


def parse_search_contacts_response(data: bytes) -> Dict[str, Any]:
    """Parse SearchContacts response.

    Response fields:
      1: users (repeated User)
      2: user_peers (repeated UserOutPeer)
      4: groups (repeated Group)
      5: group_peers (repeated GroupOutPeer)
    """
    result: Dict[str, Any] = {
        "users": [],
        "user_peers": [],
        "groups": [],
        "group_peers": [],
    }
    try:
        fields = ProtobufParser(data).parse()
        for raw in fields.get(1, []):
            parsed = parse_user(raw) if isinstance(raw, bytes) else None
            if parsed:
                result["users"].append(parsed)
        for raw in fields.get(2, []):
            peer = parse_peer(raw) if isinstance(raw, bytes) else None
            if peer:
                result["user_peers"].append(peer)
        for raw in fields.get(4, []):
            parsed = parse_group(raw) if isinstance(raw, bytes) else None
            if parsed:
                result["groups"].append(parsed)
        for raw in fields.get(5, []):
            peer = parse_peer(raw) if isinstance(raw, bytes) else None
            if peer:
                result["group_peers"].append(peer)
    except Exception as exc:
        logger.warning("parse_search_contacts_response failed: %s", exc)
    return result


def parse_import_contacts_response(data: bytes) -> Dict[str, Any]:
    """Parse ImportContacts response.

    Response fields (from bale.users.v1.Users/ImportContacts):
      1: users (repeated User)
      2: seq (int32)
      3: state (bytes)
      4: user_peers (repeated UserOutPeer)
    """
    result: Dict[str, Any] = {
        "users": [],
        "seq": 0,
        "state": b"",
        "user_peers": [],
    }
    try:
        fields = ProtobufParser(data).parse()
        for raw in fields.get(1, []):
            parsed = parse_user(raw) if isinstance(raw, bytes) else None
            if parsed:
                result["users"].append(parsed)
        if 2 in fields:
            result["seq"] = int(fields[2][0])
        if 3 in fields:
            result["state"] = fields[3][0]
        for raw in fields.get(4, []):
            peer = parse_peer(raw) if isinstance(raw, bytes) else None
            if peer:
                result["user_peers"].append(peer)
    except Exception as exc:
        logger.warning("parse_import_contacts_response failed: %s", exc)
    return result


def parse_get_contacts_response(data: bytes) -> Dict[str, Any]:
    """Parse GetContacts response.

    Response fields:
      1: users (repeated User)
      2: isNotChanged (bool)
      3: user_peers (repeated UserOutPeer)
    """
    result: Dict[str, Any] = {
        "users": [],
        "is_not_changed": False,
        "user_peers": [],
    }
    try:
        fields = ProtobufParser(data).parse()
        for raw in fields.get(1, []):
            parsed = parse_user(raw) if isinstance(raw, bytes) else None
            if parsed:
                result["users"].append(parsed)
        if 2 in fields:
            result["is_not_changed"] = bool(fields[2][0])
        for raw in fields.get(3, []):
            peer = parse_peer(raw) if isinstance(raw, bytes) else None
            if peer:
                result["user_peers"].append(peer)
    except Exception as exc:
        logger.warning("parse_get_contacts_response failed: %s", exc)
    return result
