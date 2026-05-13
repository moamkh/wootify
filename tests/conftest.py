"""
Pytest fixtures for the Wootify test suite.
Uses an in-memory SQLite database for fast, isolated tests.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_db
from app.main import app
from app.models import Base, PlatformType, Instance

TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def _create_tables():
    """Create all tables once per test session."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    """Yield a fresh database session for a single test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session):
    """Yield a FastAPI TestClient with overridden DB dependency."""
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def sample_instance(db_session):
    """Create a platform type and instance, return the instance key."""
    platform = PlatformType(
        key="bale_enterprise",
        display_name="Bale Enterprise",
        capabilities_json={},
        metadata_schema_json={},
    )
    db_session.add(platform)
    db_session.flush()

    instance = Instance(
        instance_key="test-instance",
        platform_type_id=platform.id,
        is_enabled=True,
        platform_metadata_encrypted="",
        chatwoot_config_encrypted="",
        proxy_config_encrypted="",
    )
    db_session.add(instance)
    db_session.commit()
    return instance.instance_key
