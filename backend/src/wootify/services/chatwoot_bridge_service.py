"""Compatibility alias for the relocated application service."""
import sys
from wootify.application.messaging import chatwoot_bridge as _implementation
sys.modules[__name__] = _implementation
