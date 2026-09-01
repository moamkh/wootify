import sys
from wootify.infrastructure.security import jwt as _implementation
sys.modules[__name__] = _implementation
