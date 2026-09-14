import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from core_engine.database import Base
from tests.isolation import (
    PRODUCTION_DB_NAME,
    assert_no_production_row_visibility,
    assert_test_database_url,
    assert_test_redis_url,
    database_name_from_url,
    is_production_database_url,
)


def _postgres_url() -> str | None:
    url = os.getenv("DATABASE_URL")
    return url if url and url.startswith("postgresql") else None


@pytest.fixture(autouse=True)
def _fail_fast_production_database():
    """Hard refuse any suite pointed at the live application database."""
    url = os.environ.get("DATABASE_URL") or ""
    if not url:
        return
    if is_production_database_url(url):
        raise RuntimeError(
            f"REFUSE: DATABASE_URL targets production DB '{PRODUCTION_DB_NAME}'. "
            "Isolated regression tests must use a dedicated ephemeral test database."
        )
    # Also refuse unmarked remote DSNs (legacy R8).
    allow = (os.environ.get("ALLOW_PRODUCTION_DB_MUTATION") or "").strip().lower()
    if allow in {"1", "true", "yes", "on"}:
        raise RuntimeError(
            "REFUSE: ALLOW_PRODUCTION_DB_MUTATION is set; hermetic suites must not enable it."
        )


@pytest.fixture(autouse=True)
def _disable_controlled_production_outside_guard_tests(monkeypatch, request):
    """Keep legacy suites uncapped; production-guard tests opt in explicitly."""
    if "test_production_safety_guards" in str(getattr(request, "fspath", "") or ""):
        return
    monkeypatch.setenv("CONTROLLED_PRODUCTION_ENABLED", "false")
    try:
        from core_engine.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _default_no_real_queue_push(monkeypatch, request):
    """Queue push stays off unless a test module explicitly opts in."""
    path = str(getattr(request, "fspath", "") or "")
    if "test_push_concurrency" in path or "test_incomplete_staged" in path:
        return
    if "test_phase6_controlled_dispatch" in path:
        return
    # Production-safety bridge tests opt in inside their own fixture.
    if "test_production_safety_guards" in path:
        return
    monkeypatch.setenv("REAL_QUEUE_PUSH_ENABLED", "false")
    try:
        from core_engine.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


@pytest.fixture
def pg_engine():
    url = _postgres_url()
    if not url:
        pytest.skip("DATABASE_URL not set for core_engine DB tests")
    test_db_name = assert_test_database_url(url)
    # Redis isolation is mandatory when REDIS_URL is present.
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        assert_test_redis_url(redis_url)

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            current = conn.execute(text("SELECT current_database()")).scalar()
            if str(current).lower() == PRODUCTION_DB_NAME.lower():
                raise RuntimeError(
                    f"REFUSE: connected database is production '{PRODUCTION_DB_NAME}'."
                )
            if str(current) != test_db_name:
                raise RuntimeError(
                    f"REFUSE: connected database '{current}' != expected '{test_db_name}'."
                )
            assert_no_production_row_visibility(conn)
    except pytest.skip.Exception:
        raise
    except RuntimeError:
        engine.dispose()
        raise
    except Exception:
        engine.dispose()
        pytest.skip("Postgres not reachable for core_engine DB tests")
    yield engine
    engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine):
    Base.metadata.create_all(pg_engine)
    with pg_engine.connect() as conn:
        assert_no_production_row_visibility(conn)
    return sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)
