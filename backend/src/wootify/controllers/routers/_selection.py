import sys
from wootify.presentation.http.routers import _selection as _implementation
sys.modules[__name__] = _implementation
