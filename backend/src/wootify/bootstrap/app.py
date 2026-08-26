"""FastAPI application factory and compatibility singleton."""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from wootify.bootstrap.container import ApplicationContainer
from wootify.bootstrap.lifecycle import ApplicationLifecycle
from wootify.config import settings
from wootify.presentation.http.api_v1 import router as api_v1_router
from wootify.presentation.http.panel_auth import router as panel_auth_router
from wootify.presentation.http.middleware.panel_auth import PanelAuthMiddleware
from wootify.paths import PROJECT_ROOT

logger = logging.getLogger("wootify.bootstrap.app")


async def handle_request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Log request validation failures and return the established 422 payload."""
    logger.warning(
        "request validation failed method=%s path=%s errors=%s",
        request.method,
        request.url.path,
        exc.errors(),
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
    """Log uncaught exceptions and return the established generic response."""
    logger.exception(
        "unhandled request exception method=%s path=%s error=%s",
        request.method,
        request.url.path,
        str(exc),
    )
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


async def health() -> dict[str, str]:
    """Return process liveness."""
    return {"status": "ok"}


def create_app(container: ApplicationContainer | None = None) -> FastAPI:
    """Compose a Wootify ASGI application from explicit process dependencies."""
    application_container = container or ApplicationContainer()
    lifecycle = ApplicationLifecycle(application_container)
    application = FastAPI(title="Wootify Connector API", lifespan=lifecycle.lifespan)
    application.state.container = application_container

    application.add_middleware(PanelAuthMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_v1_router)
    application.include_router(panel_auth_router)
    application.add_exception_handler(RequestValidationError, handle_request_validation_error)
    application.add_exception_handler(Exception, handle_unexpected_exception)

    if settings.LOG_HTTP_REQUESTS:

        @application.middleware("http")
        async def log_requests(request: Request, call_next):
            started_at = time.perf_counter()
            response = None
            try:
                response = await call_next(request)
                return response
            finally:
                duration_ms = (time.perf_counter() - started_at) * 1000
                logger.info(
                    "http %s %s -> %s (%.1fms)",
                    request.method,
                    request.url.path,
                    getattr(response, "status_code", "error"),
                    duration_ms,
                )

    manager_dist = PROJECT_ROOT / "frontend" / "dist"
    manager_fallback = PROJECT_ROOT / "frontend" / "static-fallback"
    manager_dir = manager_dist if manager_dist.exists() else manager_fallback
    application.mount(
        "/instance-manager",
        StaticFiles(directory=str(manager_dir), html=True),
        name="instance-manager",
    )
    application.add_api_route("/health", health, methods=["GET"])
    return application


default_container = ApplicationContainer()
default_lifecycle = ApplicationLifecycle(default_container)
lifespan = default_lifecycle.lifespan
polling_service = default_container.polling
instagram_polling_service = default_container.instagram_polling
app = create_app(default_container)

__all__ = [
    "app",
    "create_app",
    "lifespan",
    "default_container",
    "polling_service",
    "instagram_polling_service",
]
