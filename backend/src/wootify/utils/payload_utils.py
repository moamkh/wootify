import sys
from wootify.application.messaging import payload_sanitizer as _implementation
sys.modules[__name__] = _implementation
