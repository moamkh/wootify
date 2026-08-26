"""Compatibility alias for the experimental Instagram poller."""
import sys
from wootify.plugins.instagram import polling_service as _implementation
sys.modules[__name__] = _implementation
