"""Compatibility alias for the Bale enterprise SMS client."""
import sys
from wootify.plugins.bale import novin_sms_client as _implementation
sys.modules[__name__] = _implementation
