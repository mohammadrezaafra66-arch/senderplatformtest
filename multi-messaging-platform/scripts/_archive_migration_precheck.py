"""Pre-migration read-only checks for generic_archive_001."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/generic_archive_001_add_archive_columns.py"
EXPECTED_SHA256 = None  # computed at runtime for audit trail


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    import os

    url = os.environ["DATABASE_URL"]
    mig_sha = sha256_file(MIGRATION)
    print("MIGRATION_FILE", str(MIGRATION))
    print("MIGRATION_SHA256", mig_sha)

    engine = create_engine(url)
    insp = inspect(engine)
    acct_cols = {c["name"] for c in insp.get_columns("accounts")}
    camp_cols = {c["name"] for c in insp.get_columns("campaigns")}
    archive_cols = {"archived_at", "archived_by", "archive_reason"}
    acct_has = archive_cols <= acct_cols
    camp_has = archive_cols <= camp_cols
    print("ACCOUNTS_ARCHIVE_COLS_PRESENT", acct_has)
    print("CAMPAIGNS_ARCHIVE_COLS_PRESENT", camp_has)

    with engine.connect() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        print("ALEMBIC_VERSION", rev)
        acct_total = conn.execute(text("SELECT COUNT(*) FROM accounts")).scalar()
        camp_total = conn.execute(text("SELECT COUNT(*) FROM campaigns")).scalar()
        acct_arch = conn.execute(
            text("SELECT COUNT(*) FROM accounts WHERE archived_at IS NOT NULL")
        ).scalar()
        camp_arch = conn.execute(
            text("SELECT COUNT(*) FROM campaigns WHERE archived_at IS NOT NULL")
        ).scalar()
        print("ACCOUNTS_TOTAL", acct_total)
        print("CAMPAIGNS_TOTAL", camp_total)
        print("ACCOUNTS_ARCHIVED", acct_arch if acct_has else "N/A")
        print("CAMPAIGNS_ARCHIVED", camp_arch if camp_has else "N/A")

    if acct_has or camp_has:
        print("STOP: archive columns already partially present — manual review required")
        return 2
    if rev != "rubika_l3_login_challenge_001":
        print("STOP: unexpected alembic head", rev)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
