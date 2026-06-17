import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from core_engine.api.auth import get_current_user
from core_engine.database import Base, get_db
from core_engine.main import app


def _postgres_url() -> str | None:
    url = os.getenv("DATABASE_URL")
    return url if url and url.startswith("postgresql") else None


@pytest.fixture
def pg_engine():
    url = _postgres_url()
    if not url:
        pytest.skip("DATABASE_URL not set for Postgres-backed API tests")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Postgres not reachable for API tests")
    yield engine
    engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine):
    Base.metadata.create_all(pg_engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)



@pytest.fixture(autouse=True)
def api_db_override(pg_session_factory):
    def _override_get_db():
        session = pg_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def api_auth_bypass():
    async def _fake_current_user():
        return {
            "username": "operator",
            "password": "operator123",
            "role": "operator",
        }

    app.dependency_overrides[get_current_user] = _fake_current_user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def admin_auth():
    """Override auth as admin for account management tests."""

    async def _fake_admin():
        return {
            "username": "admin",
            "password": "admin123",
            "role": "admin",
        }

    app.dependency_overrides[get_current_user] = _fake_admin
    yield
    app.dependency_overrides.pop(get_current_user, None)
