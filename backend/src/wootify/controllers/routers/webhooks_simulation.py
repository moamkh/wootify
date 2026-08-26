import sys
from wootify.presentation.http.routers import webhooks_simulation as _implementation
sys.modules[__name__] = _implementation
