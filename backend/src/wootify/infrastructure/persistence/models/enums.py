"""Persistence enum values shared by ORM entities."""
import enum

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
