"""
Bale Groups Client (bale.groups.v1.Groups)
==========================================

Group and channel listing / details.
"""

import logging
from typing import Any, Dict, List, Optional

from .dialog_parser import parse_group
from .messaging_messages import Peer
from .protobuf_wire import ProtobufMessage
from .ws_client import BaleWebSocketClient

logger = logging.getLogger("bale_pv_connector.groups")


class LoadGroupsRequest:
    """Request for bale.groups.v1.Groups/LoadGroups.

    Observed payload is mostly empty; optimizations are optional.
    """

    def __init__(self, optimizations: Optional[List[int]] = None):
        self.optimizations = optimizations or []

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        if self.optimizations:
            for opt in self.optimizations:
                msg.add_int32(2, opt)
        return msg.serialize()


class GetFullGroupRequest:
    """Request for bale.groups.v1.Groups/GetFullGroup.

    Fields:
      1: groupPeer (Peer)
    """

    def __init__(self, group_id: int, access_hash: int = 0):
        self.group_id = group_id
        self.access_hash = access_hash

    def serialize(self) -> bytes:
        msg = ProtobufMessage()
        msg.add_message(1, Peer(self.group_id, Peer.PEER_TYPE_GROUP))
        return msg.serialize()


class BaleGroupsClient:
    """Client for Bale groups service (bale.groups.v1.Groups)."""

    SERVICE = "bale.groups.v1.Groups"

    def __init__(self, ws: BaleWebSocketClient):
        self.ws = ws

    async def load_groups(
        self,
        optimizations: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch group/channel list (parsed)."""
        req = LoadGroupsRequest(optimizations=optimizations)
        raw = await self.ws.send_request(
            service_name=self.SERVICE,
            method="LoadGroups",
            payload=req.serialize(),
        )
        groups: List[Dict[str, Any]] = []
        try:
            from .protobuf_wire import ProtobufParser

            fields = ProtobufParser(raw).parse()
            for raw_group in fields.get(1, []):
                parsed = parse_group(raw_group) if isinstance(raw_group, bytes) else None
                if parsed:
                    groups.append(parsed)
        except Exception as exc:
            logger.warning("load_groups parse failed: %s", exc)
        return groups

    async def get_full_group(
        self,
        group_id: int,
        access_hash: int = 0,
    ) -> bytes:
        """Fetch full group details (raw response bytes)."""
        req = GetFullGroupRequest(group_id, access_hash)
        return await self.ws.send_request(
            service_name=self.SERVICE,
            method="GetFullGroup",
            payload=req.serialize(),
        )
