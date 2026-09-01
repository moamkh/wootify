"""Compatibility alias for the relocated application service."""
import sys
from wootify.application.instances import platform_catalog as _implementation
sys.modules[__name__] = _implementation
