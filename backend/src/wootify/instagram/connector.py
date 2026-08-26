"""Compatibility alias for the experimental Instagram plugin connector."""
import sys
from wootify.plugins.instagram import connector as _implementation
sys.modules[__name__] = _implementation
