"""Apply archive columns to isolated test DB only (not production)."""
from __future__ import annotations

import os
from urllib.parse import urlparse

from sqlalchemy import create_engine, inspect, text

from tests.isolation import assert_test_database_url

STMTS = [
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS archived_by VARCHAR(128) NULL",
    "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS archive_reason VARCHAR(512) NULL",
    "CREATE INDEX IF NOT EXISTS ix_accounts_archived_at ON accounts (archived_at)",
    "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL",
    "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS archived_by VARCHAR(128) NULL",
    "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS archive_reason VARCHAR(512) NULL",
    "CREATE INDEX IF NOT EXISTS ix_campaigns_archived_at ON campaigns (archived_at)",
]


def main() -> None:
    url = os.environ["DATABASE_URL"]
    assert_test_database_url(url)
    host = (urlparse(url).hostname or "").lower()
    if host not in {"mmp_test_postgres_iso", "127.0.0.1", "localhost"}:
        raise SystemExit(f"REFUSE: unexpected host {host}")
    engine = create_engine(url)
    with engine.begin() as conn:
        for stmt in STMTS:
            conn.execute(text(stmt))
    insp = inspect(engine)
    print(
        "PASS",
        "archived_at" in {c["name"] for c in insp.get_columns("accounts")},
        "archived_at" in {c["name"] for c in insp.get_columns("campaigns")},
    )


if __name__ == "__main__":
    main()
