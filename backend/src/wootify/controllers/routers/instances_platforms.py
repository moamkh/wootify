import sys
from wootify.presentation.http.routers import instances_platforms as _implementation
sys.modules[__name__] = _implementation
