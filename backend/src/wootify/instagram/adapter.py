"""Compatibility alias for the experimental Instagram plugin adapter."""
import sys
from wootify.plugins.instagram import adapter as _implementation
sys.modules[__name__] = _implementation
