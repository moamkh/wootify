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
    enterprise_telegram_users = relationship(
        "EnterpriseTelegramUser", back_populates="instance", cascade="all, delete-orphan"
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
    bale_pv_phone_resolved_users = relationship(
        "BalePvPhoneResolvedUser",
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
    last_enterprise_sms_sync_at = Column(DateTime(timezone=True), nullable=True)
    contacts_first_synced_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    instance = relationship("Instance", back_populates="runtime_state")


class BalePvPhoneResolvedUser(Base):
    """Cache of phone numbers resolved to Bale user_id/access_hash for outbound messaging."""

    __tablename__ = "bale_pv_phone_resolved_users"
    __table_args__ = (
        UniqueConstraint(
            "instance_id", "phone_number", name="uq_bale_pv_resolved_phone"
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    instance_id = Column(
        String(36),
        ForeignKey("instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phone_number = Column(String(32), nullable=False, index=True)
    bale_user_id = Column(Integer, nullable=False)
    access_hash = Column(String(128), nullable=True)
    name = Column(String(256), nullable=True)
    nick = Column(String(256), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    instance = relationship("Instance", back_populates="bale_pv_phone_resolved_users")
