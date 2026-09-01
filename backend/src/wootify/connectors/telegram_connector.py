"""Compatibility alias for the Telegram plugin connector."""
import sys
from wootify.plugins.telegram import connector as _implementation
sys.modules[__name__] = _implementation
