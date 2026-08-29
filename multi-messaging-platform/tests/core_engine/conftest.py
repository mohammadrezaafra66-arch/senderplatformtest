import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from core_engine.database import Base


def _postgres_url() -> str | None:
    url = os.getenv("DATABASE_URL")
    return url if url and url.startswith("postgresql") else None


@pytest.fixture(autouse=True)
def _fail_fast_if_unmarked_production_dsn():
    """R8: refuse silent mutation against an unmarked non-local DATABASE_URL."""
    url = (os.environ.get("DATABASE_URL") or "").lower()
    allow = (os.environ.get("ALLOW_PRODUCTION_DB_MUTATION") or "").strip().lower()
    if allow in {"1", "true", "yes", "on"}:
        return
    if not url:
        return
    # Local compose and hermetic CI DSNs are allowed.
    if any(token in url for token in ("localhost", "127.0.0.1", "postgres", "mmp_db", "test")):
        return
    raise RuntimeError(
        "DATABASE_URL does not look like a local/test DSN; set "
        "ALLOW_PRODUCTION_DB_MUTATION=true for explicit operator-approved runs."
    )


@pytest.fixture
def pg_engine():
    url = _postgres_url()
    if not url:
        pytest.skip("DATABASE_URL not set for core_engine DB tests")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Postgres not reachable for core_engine DB tests")
    yield engine
    engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine):
    Base.metadata.create_all(pg_engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)
