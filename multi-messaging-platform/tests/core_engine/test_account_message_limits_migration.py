"""Migration upgrade/downgrade/upgrade for account message limits.

Uses only mmp_pytest_*. Aborts if the connected database is live.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

from tests.isolation import (
    FORBIDDEN_WRITE_DATABASES,
    REFUSING_NON_TEST_DATABASE,
    assert_connected_pytest_database,
)

_here = Path(__file__).resolve()
_MIGRATION_PATH = next(
    path
    for path in (
        _here.parents[2] / "alembic" / "versions" / "account_message_limits_001.py",
        _here.parents[2]
        / "multi-messaging-platform"
        / "alembic"
        / "versions"
        / "account_message_limits_001.py",
    )
    if path.is_file()
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "account_message_limits_001",
        _MIGRATION_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _column_names(conn) -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'accounts'
              AND column_name IN ('hourly_message_limit', 'daily_message_limit')
            """
        )
    )
    return {str(row[0]) for row in rows}


def _drop_limit_artifacts(conn) -> None:
    conn.execute(
        text(
            "ALTER TABLE accounts DROP CONSTRAINT IF EXISTS "
            "ck_accounts_hourly_message_limit_positive"
        )
    )
    conn.execute(
        text(
            "ALTER TABLE accounts DROP CONSTRAINT IF EXISTS "
            "ck_accounts_daily_message_limit_positive"
        )
    )
    conn.execute(text("ALTER TABLE accounts DROP COLUMN IF EXISTS hourly_message_limit"))
    conn.execute(text("ALTER TABLE accounts DROP COLUMN IF EXISTS daily_message_limit"))


def test_account_message_limits_migration_roundtrip(pg_engine):
    with pg_engine.connect() as probe:
        name = assert_connected_pytest_database(probe)
        if name.lower() in {item.lower() for item in FORBIDDEN_WRITE_DATABASES}:
            raise RuntimeError(REFUSING_NON_TEST_DATABASE)

    migration = _load_migration()
    restored = False
    try:
        with pg_engine.begin() as conn:
            assert_connected_pytest_database(conn)
            _drop_limit_artifacts(conn)
            assert _column_names(conn) == set()
            context = MigrationContext.configure(conn)
            with Operations.context(context):
                migration.upgrade()
            assert _column_names(conn) == {
                "hourly_message_limit",
                "daily_message_limit",
            }

        with pg_engine.begin() as conn:
            assert_connected_pytest_database(conn)
            context = MigrationContext.configure(conn)
            with Operations.context(context):
                migration.downgrade()
            assert _column_names(conn) == set()
            with Operations.context(context):
                migration.upgrade()
            assert _column_names(conn) == {
                "hourly_message_limit",
                "daily_message_limit",
            }
            restored = True
    finally:
        if not restored:
            with pg_engine.begin() as conn:
                context = MigrationContext.configure(conn)
                with Operations.context(context):
                    try:
                        migration.upgrade()
                    except Exception:
                        pass
