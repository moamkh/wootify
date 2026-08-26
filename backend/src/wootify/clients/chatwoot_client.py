"""Compatibility alias for the Chatwoot infrastructure client."""
import sys
from wootify.infrastructure.chatwoot import client as _implementation
sys.modules[__name__] = _implementation
