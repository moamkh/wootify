"""Compatibility alias for the Bale plugin connector."""
import sys
from wootify.plugins.bale import connector as _implementation
sys.modules[__name__] = _implementation
