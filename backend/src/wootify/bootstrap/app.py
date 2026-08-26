"""
Module Overview
---------------
Purpose: FastAPI application bootstrap, lifecycle hooks, and route mounting.
Documentation Standard: module/class/public-method docstrings.
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from wootify.config import settings
# api stuff
from wootify.controllers.api_v1_controller import _webhook_delivery_tasks
from wootify.controllers.api_v1_controller import router as api_v1_router
from wootify.controllers.panel_auth_controller import router as panel_auth_router
# db stuff
from wootify.db import SessionLocal, engine
# TODO : work on instagram polling service and find out if it works or needs deletion
# the service object created in the same file for ease of development but still beta for now
from wootify.instagram.polling_service import instagram_polling_service
# logger stuff
from wootify.logging_config import configure_logging

from wootify.models import Base
# auth middleware and service
from wootify.panel_auth.middleware import PanelAuthMiddleware
from wootify.paths import PROJECT_ROOT
from wootify.services.bale_polling_service import BalePollingService
# imported to call a static method to ensure supported platforms are registerd
from wootify.services.platform_registry_service import PlatformRegistryService
# security stuff
from wootify.utils.crypto_utils import build_previous_encryptor, encryptor
from wootify.utils.key_rotation import rotate_instance_encryption

polling_service = BalePollingService()
logger = logging.getLogger('app.main')


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan."""
    configure_logging()
    logger.info('startup: log_level=%s http_requests=%s', settings.LOG_LEVEL, settings.LOG_HTTP_REQUESTS)

    try:
        # Ensure fresh installs can start even before running alembic.
        Base.metadata.create_all(bind=engine)

        with SessionLocal() as db:
            PlatformRegistryService().ensure_seed_data(db)

        # One-shot key rotation: when DATA_ENCRYPTION_KEY_PREVIOUS is set,
        # re-encrypt stored instance secrets from the old key to the current
        # one before any service tries to read them.
        previous_encryptor = build_previous_encryptor()
        if previous_encryptor is not None:
            try:
                with SessionLocal() as db:
                    rotate_instance_encryption(db, encryptor, previous_encryptor)
            except Exception:
                logger.exception('encryption key rotation failed; continuing startup')

        await polling_service.start()
        await instagram_polling_service.start()
    except Exception:
        logger.exception('startup failed')
        raise

    try:
        yield
    finally:
        logger.info('shutdown')
        # Drain in-flight background webhook deliveries before stopping services
        # so acknowledged webhooks get a chance to finish platform delivery.
        pending_tasks = [t for t in _webhook_delivery_tasks if not t.done()]
        if pending_tasks:
            logger.info('shutdown: draining %d in-flight webhook deliveries', len(pending_tasks))
            try:
                await asyncio.wait_for(asyncio.gather(*pending_tasks, return_exceptions=True), timeout=5)
            except asyncio.TimeoutError:
                for task in pending_tasks:
                    task.cancel()
                await asyncio.gather(*pending_tasks, return_exceptions=True)
                cancelled = sum(1 for t in pending_tasks if t.cancelled())
                logger.warning('shutdown: cancelled %d in-flight webhook deliveries', cancelled)
        try:
            await instagram_polling_service.stop()
            await polling_service.stop()
        except Exception:
            logger.exception('shutdown failed')
            raise


app = FastAPI(title='Wootify Connector API', lifespan=lifespan)

# Panel auth runs inside CORS so 401 responses still carry CORS headers for
# the browser-based admin panel. It only guards /api/v1 panel endpoints;
# /api/v1/webhooks/* and /health stay public (see app/panel_auth/middleware.py).
app.add_middleware(PanelAuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(api_v1_router)
app.include_router(panel_auth_router)


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Log request validation failures and return a standard 422 payload."""
    logger.warning(
        'request validation failed method=%s path=%s errors=%s',
        request.method,
        request.url.path,
        exc.errors(),
    )
    return JSONResponse(status_code=422, content={'detail': exc.errors()})


@app.exception_handler(Exception)
async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
    """Log uncaught request exceptions and return a generic 500 payload."""
    logger.exception(
        'unhandled request exception method=%s path=%s error=%s',
        request.method,
        request.url.path,
        str(exc),
    )
    return JSONResponse(status_code=500, content={'detail': 'internal server error'})

if settings.LOG_HTTP_REQUESTS:

    @app.middleware('http')
    async def log_requests(request: Request, call_next):
        start = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                'http %s %s -> %s (%.1fms)',
                request.method,
                request.url.path,
                getattr(response, 'status_code', 'error'),
                duration_ms,
            )

repo_root = PROJECT_ROOT
manager_dist = repo_root / 'frontend' / 'dist'
manager_fallback = repo_root / 'frontend' / 'static-fallback'
manager_dir = manager_dist if manager_dist.exists() else manager_fallback

app.mount(
    '/instance-manager',
    StaticFiles(directory=str(manager_dir), html=True),
    name='instance-manager',
)


@app.get('/health')
async def health():
    """Health."""
    return {'status': 'ok'}


def create_app() -> FastAPI:
    """Return the configured ASGI application.

    The factory-shaped entrypoint is canonical for new deployments. Runtime
    state remains process-scoped for compatibility with the existing polling
    and connector lifecycle.
    """
    return app
