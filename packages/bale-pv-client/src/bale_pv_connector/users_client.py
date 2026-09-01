"""
Bale Users Client (bale.users.v1.Users)
=======================================

User management, contacts, profiles, and privacy settings.

Discovered Methods
------------------
Profile:
  GetFullUser, LoadUsers, LoadFullUsers, LoadFullUsersSequentially,
  LoadAvatars, LoadBlockedUsers

Contacts:
  GetContacts, SearchContacts, AddContact, RemoveContact,
  ImportContacts, ResetContacts, BlockUser, UnblockUser

Profile Editing:
  EditName, EditAbout, EditAvatar, RemoveAvatar, EditNickName,
  CheckNickName, EditBirthDate, EditSex, EditUserLocalName,
  EditMyPreferredLanguages, EditMyTimeZone

Privacy:
  GetUserPrivacyStatus, SetUserPrivacyStatus, GetUserFullPrivacy

Other:
  GetParameters, EditParameter, IsNameAllowed, NotifyAboutDeviceInfo,
  GetInAppUpdate
"""

import logging
from typing import Any, Dict, List, Optional

from .base_client import BaleBaseClient, RpcMethod
from .exceptions import BaleNotImplementedError

logger = logging.getLogger("bale_pv_connector.users")


class BaleUsersClient(BaleBaseClient):
    """Client for Bale users service (bale.users.v1.Users)."""

    SERVICE = "bale.users.v1.Users"

    def _method(self, name: str) -> RpcMethod:
        return RpcMethod(service_name=self.SERVICE, method_name=name)

    async def get_full_user(self, user_id: int) -> Dict[str, Any]:
        """Get detailed user profile."""
        raise BaleNotImplementedError("get_full_user requires protobuf")

    async def get_contacts(self) -> Dict[str, Any]:
        """Get contact list."""
        raise BaleNotImplementedError("get_contacts requires protobuf")

    async def search_contacts(self, query: str) -> Dict[str, Any]:
        """Search contacts by name or phone."""
        raise BaleNotImplementedError("search_contacts requires protobuf")

    async def add_contact(
        self,
        phone_number: str,
        first_name: str,
        last_name: str = "",
    ) -> Dict[str, Any]:
        """Add a contact by phone number."""
        raise BaleNotImplementedError("add_contact requires protobuf")
