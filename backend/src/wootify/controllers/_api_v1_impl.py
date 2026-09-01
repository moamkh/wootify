"""Compatibility alias for the relocated API implementation."""
import sys
from wootify.presentation.http import api_v1_impl as _implementation
sys.modules[__name__] = _implementation
