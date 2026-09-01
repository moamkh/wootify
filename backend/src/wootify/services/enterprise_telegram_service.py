"""Compatibility alias for the relocated application service."""
import sys
from wootify.application.enterprise import telegram as _implementation
sys.modules[__name__] = _implementation
