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
class BalePvPhoneMapping(Base):
    """Maps a phone number to a Bale PV user_id/access_hash for outbound messaging."""

    __tablename__ = "bale_pv_phone_mappings"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "phone_number",
            name="uq_bale_pv_phone_mapping_instance_phone",
        ),
        UniqueConstraint(
            "instance_id",
            "bale_user_id",
            name="uq_bale_pv_phone_mapping_instance_user",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    instance_id = Column(
        String(36),
        ForeignKey("instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phone_number = Column(String(64), nullable=False, index=True)
    bale_user_id = Column(String(64), nullable=False, index=True)
    access_hash = Column(String(64), nullable=True)
    display_name = Column(String(255), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    instance = relationship("Instance", backref="bale_pv_phone_mappings")


class InboundEventRetry(Base):
    """Persisted inbound platform update that failed delivery to Chatwoot.

    Rows are written by ``BalePollingService`` when a polled update exhausts
    its in-memory refetch budget (see ``_record_update_failure``) and are
    retried by the polling service retry-queue drainer until delivery
    succeeds or the owning instance is deleted.  This makes inbound delivery
    survive extended Chatwoot outages (platforms only keep unconfirmed
    updates for ~24h, so refetch alone cannot cover long downtimes).
    """

    __tablename__ = "inbound_event_retries"
    __table_args__ = (
        UniqueConstraint(
            "instance_key",
            "platform_key",
            "update_id",
            name="uq_inbound_event_retry",
        ),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    instance_key = Column(String(128), nullable=False, index=True)
    platform_key = Column(String(64), nullable=False)
    # Nullable: platform updates without a numeric id cannot be tracked via
    # the polling offset, so they are queued on first failure instead of
    # being dropped.  Rows with NULL update_id are not deduplicated.
    update_id = Column(String(64), nullable=True)
    payload_json = Column(JSON, nullable=False, default=dict)
    last_error = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    # Earliest time the drainer should retry this row (exponential backoff).
    next_attempt_at = Column(DateTime(timezone=True), nullable=True, index=True)
