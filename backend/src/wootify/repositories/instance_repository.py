"""Compatibility alias for the relocated persistence repository."""
import sys
from wootify.infrastructure.persistence.repositories import instance_repository as _implementation
sys.modules[__name__] = _implementation
