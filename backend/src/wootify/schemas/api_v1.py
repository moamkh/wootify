"""Compatibility alias for relocated HTTP schemas."""
import sys
from wootify.presentation.http.schemas import api_v1 as _implementation
sys.modules[__name__] = _implementation
