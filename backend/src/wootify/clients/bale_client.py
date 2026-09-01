"""Compatibility alias for the Bale API client."""
import sys
from wootify.plugins.bale import client as _implementation
sys.modules[__name__] = _implementation
