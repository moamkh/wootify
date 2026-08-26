"""Compatibility alias for the relocated persistence repository."""
import sys
from wootify.infrastructure.persistence.repositories import enterprise_telegram_user_repository as _implementation
sys.modules[__name__] = _implementation
