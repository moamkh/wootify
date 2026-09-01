import sys
from wootify.infrastructure.security import key_rotation as _implementation
sys.modules[__name__] = _implementation
