import pytest
from sqlalchemy import text


@pytest.mark.integration
def test_sqlite_session_executes_simple_query(sqlite_session):
    value = sqlite_session.execute(text("SELECT 1")).scalar()
    assert value == 1


@pytest.mark.integration
def test_get_db_dependency_override_wiring(override_get_db):
    value = override_get_db.execute(text("SELECT 42")).scalar()
    assert value == 42
