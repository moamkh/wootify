"""Panel authentication package.

Username/password login for the Wootify admin panel. Credentials come from
environment variables (``PANEL_AUTH_USERNAME`` / ``PANEL_AUTH_PASSWORD``) and a
successful login issues a signed JWT (HS256, dependency-free implementation)
that the panel frontend attaches to its API calls.

Scope: panel endpoints only. Webhook receivers under ``/api/v1/webhooks/`` and
service endpoints (``/health``) stay unauthenticated — see
``app/panel_auth/middleware.py`` for the exact exclusion list.
"""

from wootify.application.auth.service import PanelAuthService, panel_auth_service

__all__ = ["PanelAuthService", "panel_auth_service"]
