"""SQLAlchemy persistence entities."""
from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer, String,
    Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from wootify.infrastructure.persistence.models.base import Base, _uuid
from wootify.infrastructure.persistence.models.enums import (
    EnterpriseDocumentAssetType, EnterpriseGreStatus, EnterprisePendingMessageStatus,
    EnterpriseSessionStatus, EnterpriseUserState, MessageDirection, MessageKind,
    MessageStatus,
)
class EnterpriseBaleUser(Base):
    """Represents an enterprise Bale bot user."""

    __tablename__ = "enterprise_bale_users"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "platform_chat_id",
            name="uq_enterprise_bale_user_instance_chat",
        ),
        Index(
            "ix_enterprise_bale_users_instance_state",
            "instance_id",
            "current_state",
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
    __table_args__ = (
        Index(
            "ix_enterprise_bale_sessions_user_route_status",
            "user_id",
            "route_key",
            "status",
        ),
    )

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
    __table_args__ = (
        Index(
            "ix_enterprise_document_assets_instance_type_active_sort",
            "instance_id",
            "asset_type",
            "is_active",
            "sort_order",
        ),
    )

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


class EnterpriseTelegramUser(Base):
    """Represents an enterprise Telegram bot user."""

    __tablename__ = "enterprise_telegram_users"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "platform_chat_id",
            name="uq_enterprise_telegram_user_instance_chat",
        ),
        Index(
            "ix_enterprise_telegram_users_instance_state",
            "instance_id",
            "current_state",
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
    current_state = Column(String(64), nullable=False, default="root")
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

    instance = relationship("Instance", back_populates="enterprise_telegram_users")
    current_group = relationship("EnterpriseManualGroup")
    sessions = relationship(
        "EnterpriseTelegramSession", back_populates="user", cascade="all, delete-orphan"
    )


class EnterpriseTelegramSession(Base):
    """Represents a route-specific enterprise Telegram live-chat session."""

    __tablename__ = "enterprise_telegram_sessions"
    __table_args__ = (
        Index(
            "ix_enterprise_telegram_sessions_user_route_status",
            "user_id",
            "route_key",
            "status",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(
        String(36),
        ForeignKey("enterprise_telegram_users.id", ondelete="CASCADE"),
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

    user = relationship("EnterpriseTelegramUser", back_populates="sessions")
    pending_messages = relationship(
        "EnterpriseTelegramPendingMessage",
        back_populates="session",
        cascade="all, delete-orphan",
    )


class EnterpriseTelegramPendingMessage(Base):
    """Represents an operator message queued for later Telegram delivery."""

    __tablename__ = "enterprise_telegram_pending_messages"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "chatwoot_message_id",
            name="uq_enterprise_telegram_pending_message_session_chatwoot",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    session_id = Column(
        String(36),
        ForeignKey("enterprise_telegram_sessions.id", ondelete="CASCADE"),
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

    session = relationship("EnterpriseTelegramSession", back_populates="pending_messages")


