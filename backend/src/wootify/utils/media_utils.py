import sys
from wootify.application.messaging import media_utils as _implementation
sys.modules[__name__] = _implementation
