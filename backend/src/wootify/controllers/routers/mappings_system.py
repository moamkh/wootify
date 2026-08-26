import sys
from wootify.presentation.http.routers import mappings_system as _implementation
sys.modules[__name__] = _implementation
