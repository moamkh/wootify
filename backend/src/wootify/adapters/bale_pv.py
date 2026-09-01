"""Compatibility alias for the Bale PV plugin adapter."""
import sys
from wootify.plugins.bale_pv import adapter as _implementation
sys.modules[__name__] = _implementation
