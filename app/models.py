"""
Module Overview
---------------
Purpose: SQLAlchemy ORM models and shared persistence enums.
Documentation Standard: module/class/public-method docstrings.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


def _uuid() -> str:
    """Uuid."""
    return str(uuid.uuid4())


class MessageDirection(str, enum.Enum):
    """Represents message direction."""

    chatwoot_to_platform = "chatwoot_to_platform"
    platform_to_chatwoot = "platform_to_chatwoot"


class MessageKind(str, enum.Enum):
    """Represents message kind."""

    text = "text"
    media = "media"
    system = "system"


class MessageStatus(str, enum.Enum):
    """Represents message status."""

    pending = "pending"
    sent = "sent"
    failed = "failed"
    skipped = "skipped"


class EnterpriseGreStatus(str, enum.Enum):
    """Represents enterprise GRE validation state."""

    unknown = "unknown"
    eligible = "eligible"
    ineligible = "ineligible"


class EnterpriseUserState(str, enum.Enum):
    """Represents enterprise user state."""

    awaiting_phone_input = "awaiting_phone_input"
    eligible_root = "eligible_root"
    ineligible_root = "ineligible_root"
    manual_group_menu = "manual_group_menu"
    manual_menu = "manual_menu"
    address_menu = "address_menu"
    live_customer_service = "live_customer_service"
    live_sales = "live_sales"


class EnterpriseSessionStatus(str, enum.Enum):
    """Represents enterprise live-chat session status."""

    open = "open"
    closed_by_user = "closed_by_user"
    resolved = "resolved"


class EnterpriseDocumentAssetType(str, enum.Enum):
    """Represents enterprise document asset type."""

    manual = "manual"
    catalog = "catalog"


class EnterprisePendingMessageStatus(str, enum.Enum):
    """Represents enterprise pending message delivery status."""

    pending = "pending"
    delivered = "delivered"
    failed = "failed"


class PlatformType(Base):
    """Represents platform type."""

    __tablename__ = "platform_types"

    id = Column(String(36), primary_key=True, default=_uuid)
    key = Column(String(64), nullable=False, unique=True, index=True)
    display_name = Column(String(128), nullable=False)
    capabilities_json = Column(JSON, nullable=False, default=dict)
    metadata_schema_json = Column(JSON, nullable=False, default=dict)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    instances = relationship("Instance", back_populates="platform_type")


class FeatureDefinition(Base):
    """Represents feature definition."""

    __tablename__ = "feature_definitions"

    key = Column(String(64), primary_key=True)
    display_name = Column(String(128), nullable=False)
    description = Column(Text, nullable=False)
    default_enabled = Column(Boolean, nullable=False, default=False)
    required_platform_capability = Column(String(64), nullable=True)
    required_chatwoot_capability = Column(String(64), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    overrides = relationship("InstanceFeatureOverride", back_populates="feature")


class Instance(Base):
    """Represents instance."""

    __tablename__ = "instances"

    id = Column(String(36), primary_key=True, default=_uuid)
    instance_key = Column(String(128), nullable=False, unique=True, index=True)
    platform_type_id = Column(
        String(36),
        ForeignKey("platform_types.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    is_enabled = Column(Boolean, nullable=False, default=False)
    platform_metadata_encrypted = Column(Text, nullable=False, default="")
    chatwoot_config_encrypted = Column(Text, nullable=False, default="")
    proxy_config_encrypted = Column(Text, nullable=False, default="")
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    platform_type = relationship("PlatformType", back_populates="instances")
    feature_overrides = relationship(
        "InstanceFeatureOverride",
        back_populates="instance",
        cascade="all, delete-orphan",
    )
    runtime_state = relationship(
        "InstanceRuntimeState",
        back_populates="instance",
        uselist=False,
        cascade="all, delete-orphan",
    )
    conversations = relationship(
        "Conversation", back_populates="instance", cascade="all, delete-orphan"
    )
    enterprise_users = relationship(
        "EnterpriseBaleUser", back_populates="instance", cascade="all, delete-orphan"
    )
    enterprise_document_assets = relationship(
        "EnterpriseDocumentAsset",
        back_populates="instance",
        cascade="all, delete-orphan",
    )
    enterprise_manual_groups = relationship(
        "EnterpriseManualGroup",
        back_populates="instance",
        cascade="all, delete-orphan",
    )


class InstanceFeatureOverride(Base):
    """Represents instance feature override."""

    __tablename__ = "instance_feature_overrides"
    __table_args__ = (
        UniqueConstraint(
            "instance_id", "feature_key", name="uq_instance_feature_override"
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    instance_id = Column(
        String(36),
        ForeignKey("instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    feature_key = Column(
        String(64),
        ForeignKey("feature_definitions.key", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_enabled = Column(Boolean, nullable=False, default=False)
    effective_enabled = Column(Boolean, nullable=False, default=False)
    disabled_reason = Column(String(255), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    instance = relationship("Instance", back_populates="feature_overrides")
    feature = relationship("FeatureDefinition", back_populates="overrides")


class InstanceRuntimeState(Base):
    """Represents instance runtime state."""

    __tablename__ = "instance_runtime_state"

    instance_id = Column(
        String(36), ForeignKey("instances.id", ondelete="CASCADE"), primary_key=True
    )
    last_platform_update_id = Column(String(255), nullable=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    instance = relationship("Instance", back_populates="runtime_state")


class Conversation(Base):
    """Represents conversation."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "chatwoot_conversation_id",
            name="uq_instance_chatwoot_conversation",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    instance_id = Column(
        String(36),
        ForeignKey("instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform_conversation_id = Column(String(255), nullable=False, index=True)
    chatwoot_conversation_id = Column(String(255), nullable=False, index=True)
    chatwoot_contact_id = Column(String(255), nullable=True)
    chatwoot_inbox_id = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    last_activity_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    instance = relationship("Instance", back_populates="conversations")
    message_mappings = relationship(
        "MessageMapping", back_populates="conversation", cascade="all, delete-orphan"
    )
    runtime_state = relationship(
        "ConversationRuntimeState",
        back_populates="conversation",
        uselist=False,
        cascade="all, delete-orphan",
    )


class ConversationRuntimeState(Base):
    """Represents conversation runtime state."""

    __tablename__ = "conversation_runtime_state"

    conversation_id = Column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    last_operator_name = Column(String(255), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    conversation = relationship("Conversation", back_populates="runtime_state")


class MessageMapping(Base):
    """Represents message mapping."""

    __tablename__ = "message_mappings"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "chatwoot_message_id",
            name="uq_conversation_chatwoot_message",
        ),
        UniqueConstraint(
            "conversation_id",
            "platform_message_id",
            name="uq_conversation_platform_message",
        ),
        Index(
            "ix_message_mappings_conversation_direction_created",
            "conversation_id",
            "direction",
            "created_at",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    conversation_id = Column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    direction = Column(
        Enum(MessageDirection, native_enum=False, length=32),
        nullable=False,
    )
    chatwoot_message_id = Column(String(255), nullable=True, index=True)
    platform_message_id = Column(String(255), nullable=True, index=True)
    chatwoot_parent_message_id = Column(String(255), nullable=True)
    platform_parent_message_id = Column(String(255), nullable=True)
    message_kind = Column(
        Enum(MessageKind, native_enum=False, length=16),
        nullable=False,
        default=MessageKind.text,
    )
    status = Column(
        Enum(MessageStatus, native_enum=False, length=16),
        nullable=False,
        default=MessageStatus.pending,
    )
    error_code = Column(String(64), nullable=True)
    error_detail = Column(Text, nullable=True)
    chatwoot_payload_json = Column(JSON, nullable=True)
    platform_payload_json = Column(JSON, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    conversation = relationship("Conversation", back_populates="message_mappings")


class EnterpriseBaleUser(Base):
    """Represents an enterprise Bale bot user."""

    __tablename__ = "enterprise_bale_users"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "platform_chat_id",
            name="uq_enterprise_bale_user_instance_chat",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    instance_id = Column(
        String(36),
        ForeignKey("instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform_chat_id = Column(String(255), nullable=False, index=True)
    display_name = Column(String(255), nullable=True)
    phone_number = Column(String(64), nullable=True)
    gre_status = Column(
        Enum(EnterpriseGreStatus, native_enum=False, length=16),
        nullable=False,
        default=EnterpriseGreStatus.unknown,
    )
    current_state = Column(
        Enum(EnterpriseUserState, native_enum=False, length=64),
        nullable=False,
        default=EnterpriseUserState.awaiting_phone_input,
    )
    current_group_id = Column(
        String(36),
        ForeignKey("enterprise_manual_groups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    instance = relationship("Instance", back_populates="enterprise_users")
    current_group = relationship("EnterpriseManualGroup")
    sessions = relationship(
        "EnterpriseBaleSession", back_populates="user", cascade="all, delete-orphan"
    )


class EnterpriseBaleSession(Base):
    """Represents a route-specific enterprise live-chat session."""

    __tablename__ = "enterprise_bale_sessions"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(
        String(36),
        ForeignKey("enterprise_bale_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    route_key = Column(String(64), nullable=False, index=True)
    chatwoot_conversation_id = Column(String(255), nullable=False, index=True)
    chatwoot_contact_id = Column(String(255), nullable=True, index=True)
    chatwoot_inbox_id = Column(String(255), nullable=True)
    status = Column(
        Enum(EnterpriseSessionStatus, native_enum=False, length=24),
        nullable=False,
        default=EnterpriseSessionStatus.open,
    )
    user_present = Column(Boolean, nullable=False, default=False)
    accepted_notice_sent = Column(Boolean, nullable=False, default=False)
    unread_notice_sent = Column(Boolean, nullable=False, default=False)
    unread_count = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user = relationship("EnterpriseBaleUser", back_populates="sessions")
    pending_messages = relationship(
        "EnterprisePendingMessage",
        back_populates="session",
        cascade="all, delete-orphan",
    )


class EnterprisePendingMessage(Base):
    """Represents an operator message queued for later Bale delivery."""

    __tablename__ = "enterprise_bale_pending_messages"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "chatwoot_message_id",
            name="uq_enterprise_pending_message_session_chatwoot",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    session_id = Column(
        String(36),
        ForeignKey("enterprise_bale_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chatwoot_message_id = Column(String(255), nullable=True, index=True)
    text_payload = Column(Text, nullable=True)
    attachment_payload_json = Column(JSON, nullable=True)
    status = Column(
        Enum(EnterprisePendingMessageStatus, native_enum=False, length=16),
        nullable=False,
        default=EnterprisePendingMessageStatus.pending,
    )
    delivery_error = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    session = relationship("EnterpriseBaleSession", back_populates="pending_messages")


class EnterpriseDocumentAsset(Base):
    """Represents a stored enterprise manual or catalog asset."""

    __tablename__ = "enterprise_document_assets"

    id = Column(String(36), primary_key=True, default=_uuid)
    instance_id = Column(
        String(36),
        ForeignKey("instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_type = Column(
        Enum(EnterpriseDocumentAssetType, native_enum=False, length=16),
        nullable=False,
        index=True,
    )
    display_name = Column(String(255), nullable=True)
    link_url = Column(String(1024), nullable=False, default='')
    storage_path = Column(String(512), nullable=False)
    original_filename = Column(String(255), nullable=False)
    content_type = Column(String(255), nullable=True)
    size_bytes = Column(Integer, nullable=False, default=0)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    instance = relationship("Instance", back_populates="enterprise_document_assets")
    group_assignments = relationship(
        "EnterpriseManualGroupAssignment",
        back_populates="asset",
        cascade="all, delete-orphan",
    )


class EnterpriseManualGroup(Base):
    """Represents a group/category of enterprise manuals."""

    __tablename__ = "enterprise_manual_groups"

    id = Column(String(36), primary_key=True, default=_uuid)
    instance_id = Column(
        String(36),
        ForeignKey("instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("instance_id", "name", name="uq_enterprise_manual_group_instance_name"),
        Index("ix_enterprise_manual_groups_sort_order", "instance_id", "sort_order"),
    )

    instance = relationship("Instance", back_populates="enterprise_manual_groups")
    assignments = relationship(
        "EnterpriseManualGroupAssignment",
        back_populates="group",
        cascade="all, delete-orphan",
    )


class EnterpriseManualGroupAssignment(Base):
    """Represents an assignment of a manual to a group."""

    __tablename__ = "enterprise_manual_group_assignments"

    id = Column(String(36), primary_key=True, default=_uuid)
    group_id = Column(
        String(36),
        ForeignKey("enterprise_manual_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    asset_id = Column(
        String(36),
        ForeignKey("enterprise_document_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("group_id", "asset_id", name="uq_enterprise_manual_group_assignment_group_asset"),
        Index("ix_enterprise_manual_group_assignments_sort_order", "group_id", "sort_order"),
    )

    group = relationship("EnterpriseManualGroup", back_populates="assignments")
    asset = relationship("EnterpriseDocumentAsset", back_populates="group_assignments")
