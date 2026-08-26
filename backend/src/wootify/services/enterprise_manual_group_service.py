"""Compatibility alias for the relocated application service."""
import sys
from wootify.application.enterprise import manual_groups as _implementation
sys.modules[__name__] = _implementation
