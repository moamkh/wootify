"""
Module Overview
---------------
Purpose: FastAPI application bootstrap, lifecycle hooks, and route mounting.
Documentation Standard: module/class/public-method docstrings.
"""
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.controllers.api_v1_controller import router as api_v1_router
from app.db import SessionLocal, engine
from app.logging_config import configure_logging
from app.models import Base
from app.services.bale_polling_service import BalePollingService
from app.services.platform_registry_service import PlatformRegistryService

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

        await polling_service.start()
    except Exception:
        logger.exception('startup failed')
        raise

    try:
        yield
    finally:
        logger.info('shutdown')
        try:
            await polling_service.stop()
        except Exception:
            logger.exception('shutdown failed')
            raise


app = FastAPI(title='Wootify Connector API', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(api_v1_router)


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

repo_root = Path(__file__).resolve().parents[1]
manager_dist = repo_root / 'wootify-instance-manager' / 'dist'
manager_fallback = repo_root / 'wootify-instance-manager' / 'static-fallback'
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

