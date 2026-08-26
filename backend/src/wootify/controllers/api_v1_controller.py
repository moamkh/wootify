"""Compatibility alias for the focused API facade."""
import sys
from wootify.presentation.http import api_v1 as _implementation
sys.modules[__name__] = _implementation
