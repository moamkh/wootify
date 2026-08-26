import sys
from wootify.presentation.http.middleware import panel_auth as _implementation
sys.modules[__name__] = _implementation
