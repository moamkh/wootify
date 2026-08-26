"""Compatibility alias for the relocated panel-auth router."""
import sys
from wootify.presentation.http import panel_auth as _implementation
sys.modules[__name__] = _implementation
