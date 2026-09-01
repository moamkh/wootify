import sys
from wootify.infrastructure.security import crypto as _implementation
sys.modules[__name__] = _implementation
