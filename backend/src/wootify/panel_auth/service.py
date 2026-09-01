import sys
from wootify.application.auth import service as _implementation
sys.modules[__name__] = _implementation
