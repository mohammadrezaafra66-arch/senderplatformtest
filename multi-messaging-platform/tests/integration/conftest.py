"""Fixtures for integration tests."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from core_engine.database import get_db
from core_engine.main import app
from core_engine.tasks import celery_app


@pytest.fixture
def integration_client():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def celery_eager():
    """Run Celery tasks synchronously in-process for wiring checks."""
    previous_always_eager = celery_app.conf.task_always_eager
    previous_eager_propagates = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    try:
        yield celery_app
    finally:
        celery_app.conf.task_always_eager = previous_always_eager
        celery_app.conf.task_eager_propagates = previous_eager_propagates


@pytest.fixture
def sqlite_session() -> Generator[Session, None, None]:
    """Isolated in-memory SQLite session for DB wiring smoke tests."""
    engine = create_engine("sqlite:///:memory:")
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def override_get_db(sqlite_session: Session):
    """Override FastAPI get_db with the in-memory SQLite session."""

    def _get_test_db() -> Generator[Session, None, None]:
        try:
            yield sqlite_session
        finally:
            pass

    app.dependency_overrides[get_db] = _get_test_db
    try:
        yield sqlite_session
    finally:
        app.dependency_overrides.pop(get_db, None)
