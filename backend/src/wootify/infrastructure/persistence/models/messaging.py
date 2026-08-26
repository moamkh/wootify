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
class Conversation(Base):
    """Represents conversation."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "chatwoot_conversation_id",
            name="uq_instance_chatwoot_conversation",
        ),
        Index(
            "ix_conversations_instance_platform_active",
            "instance_id",
            "platform_conversation_id",
            "is_active",
        ),
        Index(
            "ix_conversations_instance_chatwoot_active",
            "instance_id",
            "chatwoot_conversation_id",
            "is_active",
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


