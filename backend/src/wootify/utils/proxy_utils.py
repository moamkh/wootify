import sys
from wootify.infrastructure.network import proxy as _implementation
sys.modules[__name__] = _implementation
