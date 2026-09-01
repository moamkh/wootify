import sys
from wootify.application.shared import legacy_cache as _implementation
sys.modules[__name__] = _implementation
