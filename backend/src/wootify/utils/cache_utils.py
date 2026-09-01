import sys
from wootify.application.shared import cache as _implementation
sys.modules[__name__] = _implementation
