"""Compatibility alias for the Bale PV plugin connector."""
import sys
from wootify.plugins.bale_pv import connector as _implementation
sys.modules[__name__] = _implementation
