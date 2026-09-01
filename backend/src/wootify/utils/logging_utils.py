import sys
from wootify.infrastructure.observability import logging as _implementation
sys.modules[__name__] = _implementation
