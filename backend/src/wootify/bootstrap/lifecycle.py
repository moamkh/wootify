"""Object-owned FastAPI startup and shutdown lifecycle."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from fastapi import FastAPI

from wootify.config import settings
from wootify.presentation.http.api_v1 import _webhook_delivery_tasks
from wootify.infrastructure.persistence.session import SessionLocal, engine
from wootify.logging_config import configure_logging
from wootify.infrastructure.persistence.models import Base
from wootify.infrastructure.security.crypto import build_previous_encryptor, encryptor
from wootify.infrastructure.security.key_rotation import rotate_instance_encryption

if TYPE_CHECKING:
    from wootify.bootstrap.container import ApplicationContainer

logger = logging.getLogger("wootify.bootstrap.lifecycle")


class ApplicationLifecycle:
    """Coordinate process-wide resources owned by an application container."""

    def __init__(self, container: ApplicationContainer) -> None:
        self.container = container

    def initialize_database(self) -> None:
        Base.metadata.create_all(bind=engine)
        with SessionLocal() as db:
            self.container.platform_catalog.ensure_seed_data(db)
        previous_encryptor = build_previous_encryptor()
        if previous_encryptor is not None:
            try:
                with SessionLocal() as db:
                    rotate_instance_encryption(db, encryptor, previous_encryptor)
            except Exception:
                logger.exception("encryption key rotation failed; continuing startup")

    async def start(self) -> None:
        configure_logging()
        logger.info(
            "startup: log_level=%s http_requests=%s",
            settings.LOG_LEVEL,
            settings.LOG_HTTP_REQUESTS,
        )
        self.initialize_database()
        await self.container.polling.start()
        await self.container.instagram_polling.start()

    async def stop(self) -> None:
        logger.info("shutdown")
        pending_tasks = [task for task in _webhook_delivery_tasks if not task.done()]
        if pending_tasks:
            logger.info("shutdown: draining %d in-flight webhook deliveries", len(pending_tasks))
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending_tasks, return_exceptions=True), timeout=5
                )
            except asyncio.TimeoutError:
                for task in pending_tasks:
                    task.cancel()
                await asyncio.gather(*pending_tasks, return_exceptions=True)
                cancelled = sum(1 for task in pending_tasks if task.cancelled())
                logger.warning("shutdown: cancelled %d in-flight webhook deliveries", cancelled)
        await self.container.instagram_polling.stop()
        await self.container.polling.stop()

    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        try:
            await self.start()
        except Exception:
            logger.exception("startup failed")
            raise
        try:
            yield
        finally:
            try:
                await self.stop()
            except Exception:
                logger.exception("shutdown failed")
                raise
