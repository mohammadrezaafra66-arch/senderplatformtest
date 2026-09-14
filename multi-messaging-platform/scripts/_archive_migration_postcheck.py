"""Post-migration verification for generic_archive_001."""
from __future__ import annotations

import os

from sqlalchemy import create_engine, inspect, text


def main() -> int:
    url = os.environ["DATABASE_URL"]
    engine = create_engine(url)
    insp = inspect(engine)
    acct_cols = {c["name"] for c in insp.get_columns("accounts")}
    camp_cols = {c["name"] for c in insp.get_columns("campaigns")}
    need = {"archived_at", "archived_by", "archive_reason"}
    acct_ok = need <= acct_cols
    camp_ok = need <= camp_cols
    print("ACCOUNT_ARCHIVE_COLUMNS_LIVE", acct_ok)
    print("CAMPAIGN_ARCHIVE_COLUMNS_LIVE", camp_ok)

    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        print("ALEMBIC_VERSION", rev)
        acct_arch = conn.execute(
            text("SELECT COUNT(*) FROM accounts WHERE archived_at IS NOT NULL")
        ).scalar()
        camp_arch = conn.execute(
            text("SELECT COUNT(*) FROM campaigns WHERE archived_at IS NOT NULL")
        ).scalar()
        acct_null = conn.execute(
            text("SELECT COUNT(*) FROM accounts WHERE archived_at IS NULL")
        ).scalar()
        camp_null = conn.execute(
            text("SELECT COUNT(*) FROM campaigns WHERE archived_at IS NULL")
        ).scalar()
        acct_total = conn.execute(text("SELECT COUNT(*) FROM accounts")).scalar()
        camp_total = conn.execute(text("SELECT COUNT(*) FROM campaigns")).scalar()

    print("EXISTING_ACCOUNTS_ARCHIVED_COUNT", int(acct_arch))
    print("EXISTING_CAMPAIGNS_ARCHIVED_COUNT", int(camp_arch))
    print("ACCOUNTS_ARCHIVED_AT_NULL", int(acct_null))
    print("CAMPAIGNS_ARCHIVED_AT_NULL", int(camp_null))
    print("ACCOUNTS_TOTAL", int(acct_total))
    print("CAMPAIGNS_TOTAL", int(camp_total))
    print("MIGRATION_DATA_LOSS", 0 if acct_null == acct_total and camp_null == camp_total else 1)

    ok = (
        acct_ok
        and camp_ok
        and rev == "generic_archive_001"
        and acct_arch == 0
        and camp_arch == 0
    )
    print("DB_MIGRATION_PASS", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
