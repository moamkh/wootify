"""
Module Overview
---------------
Purpose: Pydantic request/response schema contracts used by API endpoints.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class PlatformTypeResponse(BaseModel):
    """Response schema for platform type operations."""
    id: str
    key: str
    display_name: str
    capabilities: dict[str, Any]
    metadata_schema: dict[str, Any]
    is_active: bool


class FeatureDefinitionResponse(BaseModel):
    """Response schema for feature definition operations."""
    key: str
    display_name: str
    description: str
    default_enabled: bool
    required_platform_capability: Optional[str] = None
    required_chatwoot_capability: Optional[str] = None


class FeatureOverrideResponse(BaseModel):
    """Response schema for feature override operations."""
    feature_key: str
    requested_enabled: bool
    effective_enabled: bool
    disabled_reason: Optional[str] = None


class AutoCreateInboxResponse(BaseModel):
    """Response schema for auto-create inbox attempts during instance save."""
    attempted: bool
    created: bool = False
    inbox_id: Optional[int] = None
    detail: Optional[str] = None


class EnterpriseAutoCreateInboxResponse(BaseModel):
    """Response schema for enterprise route auto-create inbox attempts."""
    route_key: str
    attempted: bool
    created: bool = False
    inbox_id: Optional[int] = None
    detail: Optional[str] = None


class ProxyConfigRequest(BaseModel):
    """Request schema for proxy config operations."""
    enabled: bool = False
    protocol: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None


class ProxyConfigResponse(BaseModel):
    """Response schema for proxy config operations."""
    enabled: bool
    protocol: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None


class InstanceCreateRequest(BaseModel):
    """Request schema for instance create operations."""
    instance_key: str
    platform_type_key: str = 'bale'
    is_enabled: bool = True
    platform_metadata: dict[str, Any] = Field(default_factory=dict)
    chatwoot: dict[str, Any] = Field(default_factory=dict)
    proxy: ProxyConfigRequest = Field(default_factory=ProxyConfigRequest)
    feature_overrides: dict[str, bool] = Field(default_factory=dict)


class InstancePatchRequest(BaseModel):
    """Request schema for instance patch operations."""
    is_enabled: Optional[bool] = None
    platform_type_key: Optional[str] = None
    platform_metadata: Optional[dict[str, Any]] = None
    chatwoot: Optional[dict[str, Any]] = None
    proxy: Optional[ProxyConfigRequest] = None
    feature_overrides: Optional[dict[str, bool]] = None


class InstanceResponse(BaseModel):
    """Response schema for instance operations."""
    id: str
    instance_key: str
    platform_type_key: str
    platform_display_name: str
    is_enabled: bool
    platform_metadata: dict[str, Any]
    chatwoot: dict[str, Any]
    proxy: ProxyConfigResponse
    feature_overrides: list[FeatureOverrideResponse]
    auto_create_inbox: Optional[AutoCreateInboxResponse] = None
    enterprise_auto_create_inboxes: Optional[list[EnterpriseAutoCreateInboxResponse]] = None
    created_at: datetime
    updated_at: datetime


class InstanceListResponse(BaseModel):
    """Response schema for instance list operations."""
    items: list[InstanceResponse]


class ConversationResponse(BaseModel):
    """Response schema for conversation operations."""
    id: str
    instance_id: str
    platform_conversation_id: str
    chatwoot_conversation_id: str
    chatwoot_contact_id: Optional[str] = None
    chatwoot_inbox_id: Optional[str] = None
    is_active: bool = True
    last_activity_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    """Response schema for conversation list operations."""
    items: list[ConversationResponse]


class MessageMappingResponse(BaseModel):
    """Response schema for message mapping operations."""
    id: str
    conversation_id: str
    direction: str
    chatwoot_message_id: Optional[str] = None
    platform_message_id: Optional[str] = None
    chatwoot_parent_message_id: Optional[str] = None
    platform_parent_message_id: Optional[str] = None
    message_kind: str
    status: str
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MessageMappingListResponse(BaseModel):
    """Response schema for message mapping list operations."""
    items: list[MessageMappingResponse]


class SimulatedAttachment(BaseModel):
    """Represents simulated attachment."""
    filename: str
    content_base64: str
    content_type: Optional[str] = None


class SimulatePlatformEventRequest(BaseModel):
    """Request schema for simulate platform event operations."""
    chat_id: str
    text: str = ''
    from_name: Optional[str] = None
    platform_message_id: Optional[str] = None
    parent_platform_message_id: Optional[str] = None
    attachments: list[SimulatedAttachment] = Field(default_factory=list)


class GenericMessageResponse(BaseModel):
    """Response schema for generic message operations."""
    message: str
    detail: Optional[str] = None
    status: Optional[str] = None


class CreateInboxResponse(BaseModel):
    """Response schema for create inbox operations."""
    created: bool
    inbox_id: Optional[int] = None
    inbox: Optional[dict[str, Any]] = None


class EnterpriseDocumentAssetResponse(BaseModel):
    """Response schema for enterprise document assets."""
    id: str
    asset_type: str
    display_name: Optional[str] = None
    link_url: str
    original_filename: str
    content_type: Optional[str] = None
    size_bytes: int
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class EnterpriseDocumentListResponse(BaseModel):
    """Response schema for enterprise document list operations."""
    items: list[EnterpriseDocumentAssetResponse]


class EnterpriseDocumentAssetPatchRequest(BaseModel):
    """Patch request schema for enterprise document asset metadata."""
    display_name: Optional[str] = Field(None, min_length=1, max_length=255)
    link_url: Optional[str] = None


class EnterpriseCatalogResponse(BaseModel):
    """Response schema for the active enterprise catalog."""
    item: Optional[EnterpriseDocumentAssetResponse] = None


class EnterpriseRouteInboxResponse(BaseModel):
    """Response schema for enterprise route inbox creation/linking."""
    route_key: str
    created: bool
    webhook_url: Optional[str] = None
    inbox_id: Optional[int] = None
    inbox: Optional[dict[str, Any]] = None


class EnterpriseSessionResponse(BaseModel):
    """Response schema for enterprise live-chat sessions."""
    id: str
    route_key: str
    platform_chat_id: str
    display_name: Optional[str] = None
    phone_number: Optional[str] = None
    gre_status: Optional[str] = None
    current_state: str
    chatwoot_conversation_id: str
    chatwoot_contact_id: Optional[str] = None
    chatwoot_inbox_id: Optional[str] = None
    status: str
    user_present: bool
    accepted_notice_sent: bool
    unread_notice_sent: bool
    unread_count: int
    created_at: datetime
    updated_at: datetime


class EnterpriseSessionListResponse(BaseModel):
    """Response schema for enterprise session list operations."""
    items: list[EnterpriseSessionResponse]


class EnterpriseSmsSyncConfigResponse(BaseModel):
    """Response schema for enterprise SMS sync configuration."""
    enabled: bool
    api_url: str
    token_header: str
    token_prefix: str = ''
    poll_interval_minutes: int
    last_id: int
    http_timeout_seconds: int
    api_token_configured: bool


class EnterpriseSmsSyncConfigPatchRequest(BaseModel):
    """Patch request schema for enterprise SMS sync configuration."""
    enabled: Optional[bool] = None
    api_url: Optional[str] = None
    api_token: Optional[str] = None
    token_header: Optional[str] = None
    token_prefix: Optional[str] = None
    poll_interval_minutes: Optional[int] = None
    last_id: Optional[int] = None
    http_timeout_seconds: Optional[int] = None


class EnterpriseSmsSyncRunResponse(BaseModel):
    """Response schema for enterprise SMS sync manual run."""
    message: str
    fetched: int = 0
    delivered: int = 0
    dropped: int = 0
    failed: int = 0
    last_id: Optional[int] = None
    detail: Optional[str] = None


class EnterpriseManualGroupResponse(BaseModel):
    """Response schema for enterprise manual groups."""
    id: str
    name: str
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class EnterpriseManualGroupListResponse(BaseModel):
    """Response schema for enterprise manual groups list operations."""
    items: list[EnterpriseManualGroupResponse]


class EnterpriseManualGroupCreateRequest(BaseModel):
    """Create request schema for enterprise manual groups."""
    name: str = Field(..., min_length=1, max_length=255)


class EnterpriseManualGroupUpdateRequest(BaseModel):
    """Update request schema for enterprise manual groups."""
    name: str = Field(..., min_length=1, max_length=255)


class EnterpriseManualGroupManualsResponse(BaseModel):
    """Response schema for manuals in an enterprise manual group."""
    items: list[EnterpriseDocumentAssetResponse]


class EnterpriseManualGroupWithManualsResponse(BaseModel):
    """Response schema for an enterprise manual group including its manuals."""
    id: str
    name: str
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    manuals: list[EnterpriseDocumentAssetResponse]


class EnterpriseManualGroupsWithManualsResponse(BaseModel):
    """Response schema for all enterprise manual groups with their manuals."""
    groups: list[EnterpriseManualGroupWithManualsResponse]
    manual_group_map: dict[str, str]


class BalePvContact(BaseModel):
    """A single Bale PV contact."""
    id: int
    name: str = ""
    nick: str = ""


class BalePvContactsResponse(BaseModel):
    """Response schema for Bale PV contacts list."""
    contacts: list[BalePvContact]


class BalePvSyncContactsResponse(BaseModel):
    """Response schema for Bale PV contacts sync operation."""
    message: str
    detail: Optional[str] = None
    created: int = 0
    updated: int = 0
    failed: int = 0


class BalePvDialog(BaseModel):
    """A single Bale PV dialog/conversation."""
    peer_id: int
    peer_type: int = 1
    unread_count: int = 0
    text: str = ""
    date: Optional[int] = None


class BalePvDialogsResponse(BaseModel):
    """Response schema for Bale PV dialogs list."""
    dialogs: list[BalePvDialog]


class BalePvSyncDialogsResponse(BaseModel):
    """Response schema for Bale PV dialogs sync operation."""
    message: str
    detail: Optional[str] = None
    created: int = 0
    updated: int = 0
    failed: int = 0
    dialogs: int = 0
    messages_imported: int = 0


class BalePvRemoveChatwootContactsResponse(BaseModel):
    """Response schema for removing Bale PV contacts from Chatwoot."""
    message: str
    detail: Optional[str] = None
    deleted: int = 0
    failed: int = 0
    skipped: int = 0
    total_bale: int = 0
    total_chatwoot: int = 0
    dry_run: bool = False
