"""Compatibility alias for the relocated application service."""
import sys
from wootify.application.instances import service as _implementation
sys.modules[__name__] = _implementation
