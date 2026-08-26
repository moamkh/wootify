"""Application objects for platform/Chatwoot message orchestration."""

from wootify.application.messaging.destination_resolver import DestinationResolver
from wootify.application.messaging.media_normalizer import MediaNormalizer
from wootify.application.messaging.payload_parser import (
    ChatwootPayloadParser,
    MessagePayloadParser,
)
from wootify.application.messaging.notification_policy import NotificationPolicy

__all__ = [
    "ChatwootPayloadParser",
    "DestinationResolver",
    "MediaNormalizer",
    "MessagePayloadParser",
    "NotificationPolicy",
]
