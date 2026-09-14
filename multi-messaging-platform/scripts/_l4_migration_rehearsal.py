#!/usr/bin/env python3
"""L4 production migration rehearsal — HARDENED (isolated clone only).

Safety contract (source):
  - Production mmp_db is READ-ONLY (pg_dump / SELECT 1 only).
  - Schema mutations target ONLY exact allowlisted DBs:
      mmp_l4_rehearsal
      mmp_l4_rehearsal_downgrade
  - Alembic runs in one-shot container mmp_l4_alembic_runner (NOT mmp_core_api).
  - Host source tree is mounted into the runner (no docker cp into production API).
  - No Redis, no workers, no OTP, no Rubika network.

This script does NOT auto-run when imported. Operator must invoke main()
explicitly after approving the hardening audit.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

PRODUCTION_DB = "mmp_db"
REHEARSAL_DB = "mmp_l4_rehearsal"
DOWNGRADE_DB = "mmp_l4_rehearsal_downgrade"
REHEARSAL_DB_ALLOWLIST = frozenset({REHEARSAL_DB, DOWNGRADE_DB})

# Explicit production identifiers that must never be mutation targets.
PRODUCTION_DB_DENYLIST = frozenset(
    {
        PRODUCTION_DB,
        "mmp_db",
        "postgres",  # admin catalog — never treat as rehearsal target
        "template0",
        "template1",
    }
)

PG_USER = os.environ.get("PGUSER", "mmp_user")
PG_PASSWORD = os.environ.get("PGPASSWORD", "mmp_pass")
PG_HOST_CONTAINER = os.environ.get("PGHOST", "mmp_postgres")
# Inside compose network the postgres service hostname is "postgres".
PG_SERVICE_HOSTNAME = os.environ.get("L4_PG_SERVICE_HOSTNAME", "postgres")
COMPOSE_NETWORK = os.environ.get("L4_REHEARSAL_NETWORK", "multi-messaging-platform_default")
ALEMBIC_RUNNER_NAME = "mmp_l4_alembic_runner"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = PROJECT_ROOT / "reports" / "rubika-remediation"
BACKUP_DIR = REPORT_DIR / "l4_rehearsal"
ARTIFACT = REPORT_DIR / "L4_MIGRATION_REHEARSAL.json"

PROTECTED_ACCOUNTS = (12, 79)
DUPLICATE_ACCOUNTS = (1, 2, 12, 19, 79, 81, 92)


# ---------------------------------------------------------------------------
# Authoritative mutation-target guard
# ---------------------------------------------------------------------------


def assert_rehearsal_db_name(db_name: str) -> str:
    """Hard-fail unless db_name is an exact allowlisted disposable rehearsal DB.

    Never accepts production identifiers (including mmp_db / PRODUCTION_DB).
    Never accepts wildcards or partial matches.
    """
    name = str(db_name or "").strip()
    if not name:
        raise RuntimeError("REFUSE: empty database name is not a rehearsal target")
    if name == PRODUCTION_DB or name in PRODUCTION_DB_DENYLIST:
        raise RuntimeError(
            f"REFUSE: production/system database '{name}' is never a rehearsal "
            "mutation target"
        )
    if name.lower() == PRODUCTION_DB.lower():
        raise RuntimeError(f"REFUSE: production database '{name}' (case-insensitive)")
    if name not in REHEARSAL_DB_ALLOWLIST:
        raise RuntimeError(
            f"REFUSE: database '{name}' is not in exact rehearsal allowlist "
            f"{sorted(REHEARSAL_DB_ALLOWLIST)}"
        )
    return name


def assert_database_url_is_rehearsal(database_url: str) -> str:
    """Parse DATABASE_URL and hard-refuse if the path is not an allowlisted rehearsal DB."""
    parsed = urlparse(database_url)
    db_name = (parsed.path or "").lstrip("/").split("?")[0].strip()
    assert_rehearsal_db_name(db_name)
    if PRODUCTION_DB in db_name.split("/") or db_name.lower() == PRODUCTION_DB.lower():
        raise RuntimeError(f"REFUSE: DATABASE_URL targets production: {database_url!r}")
    return db_name


def rehearsal_database_url(db_name: str) -> str:
    """Build an explicit rehearsal DATABASE_URL — never inherits production URL."""
    name = assert_rehearsal_db_name(db_name)
    # Constructed from constants only — does not read os.environ DATABASE_URL.
    return (
        f"postgresql://{PG_USER}:{PG_PASSWORD}"
        f"@{PG_SERVICE_HOSTNAME}:5432/{name}"
    )


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, check=check, capture_output=True, text=True, env=env)


def _docker_psql_admin(sql: str) -> str:
    """Run SQL against the admin 'postgres' catalog (never a rehearsal mutation target itself)."""
    r = _run(
        [
            "docker",
            "exec",
            PG_HOST_CONTAINER,
            "psql",
            "-U",
            PG_USER,
            "-d",
            "postgres",
            "-tAc",
            sql,
        ]
    )
    return (r.stdout or "").strip()


def _docker_psql(db: str, sql: str) -> str:
    """Read-only queries against a named database.

    Production SELECT is allowed only for connectivity / inventory reads.
    Mutation SQL must never be passed here for PRODUCTION_DB.
    """
    name = str(db or "").strip()
    if name in REHEARSAL_DB_ALLOWLIST:
        assert_rehearsal_db_name(name)
    elif name == PRODUCTION_DB:
        # Read-only path: refuse obvious DDL keywords.
        upper = sql.strip().upper()
        if any(
            upper.startswith(tok)
            for tok in (
                "DROP ",
                "CREATE ",
                "ALTER ",
                "TRUNCATE ",
                "INSERT ",
                "UPDATE ",
                "DELETE ",
                "GRANT ",
                "REVOKE ",
            )
        ):
            raise RuntimeError("REFUSE: DDL/DML against production mmp_db is forbidden")
    else:
        raise RuntimeError(
            f"REFUSE: psql target '{name}' is neither production (read-only) "
            f"nor an allowlisted rehearsal DB"
        )
    r = _run(
        [
            "docker",
            "exec",
            PG_HOST_CONTAINER,
            "psql",
            "-U",
            PG_USER,
            "-d",
            name,
            "-tAc",
            sql,
        ]
    )
    return (r.stdout or "").strip()


# ---------------------------------------------------------------------------
# Disposable DB lifecycle (exact names only)
# ---------------------------------------------------------------------------


def _drop_create(db: str) -> None:
    """DROP/CREATE only allowlisted rehearsal databases."""
    name = assert_rehearsal_db_name(db)
    # Terminate backends so DROP succeeds even after failed partial migrations.
    _docker_psql_admin(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{name}' AND pid <> pg_backend_pid();"
    )
    _docker_psql_admin(f'DROP DATABASE IF EXISTS "{name}";')
    _docker_psql_admin(f'CREATE DATABASE "{name}" OWNER {PG_USER};')


def reset_stale_rehearsal_databases() -> None:
    """DROP+CREATE both disposable rehearsal DBs (exact allowlist only).

    Required because prior failed attempts may leave partial schema state.
    Never touches mmp_db.
    """
    for name in (REHEARSAL_DB, DOWNGRADE_DB):
        assert_rehearsal_db_name(name)
        print(f"event=stale_rehearsal_reset db={name}")
        _drop_create(name)


# ---------------------------------------------------------------------------
# Production read-only backup + restore into disposable DBs
# ---------------------------------------------------------------------------


def _backup_production() -> Path:
    """pg_dump production mmp_db — READ ONLY. Records path/sha256/timestamp."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump_path = BACKUP_DIR / f"mmp_db_pre_l4_{ts}.dump"
    _run(
        [
            "docker",
            "exec",
            PG_HOST_CONTAINER,
            "pg_dump",
            "-U",
            PG_USER,
            "-Fc",
            "-f",
            f"/tmp/{dump_path.name}",
            PRODUCTION_DB,
        ]
    )
    _run(
        [
            "docker",
            "cp",
            f"{PG_HOST_CONTAINER}:/tmp/{dump_path.name}",
            str(dump_path),
        ]
    )
    digest = hashlib.sha256(dump_path.read_bytes()).hexdigest()
    meta = {
        "path": str(dump_path),
        "sha256": digest,
        "created_at": ts,
        "source_db": PRODUCTION_DB,
        "source_mode": "pg_dump_read_only",
        "mutation": False,
    }
    (BACKUP_DIR / f"{dump_path.name}.meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    print(f"backup={dump_path} sha256={digest} source={PRODUCTION_DB} mode=read_only")
    return dump_path


def _restore(dump_path: Path, target_db: str) -> None:
    """Restore dump into an exact allowlisted rehearsal DB only."""
    name = assert_rehearsal_db_name(target_db)
    _drop_create(name)
    container_dump = f"/tmp/{dump_path.name}"
    _run(["docker", "cp", str(dump_path), f"{PG_HOST_CONTAINER}:{container_dump}"])
    _run(
        [
            "docker",
            "exec",
            PG_HOST_CONTAINER,
            "pg_restore",
            "-U",
            PG_USER,
            "-d",
            name,
            "--no-owner",
            "--no-acl",
            container_dump,
        ],
        check=False,
    )


# ---------------------------------------------------------------------------
# Isolated Alembic runner (NOT mmp_core_api)
# ---------------------------------------------------------------------------


def _resolve_alembic_runner_image() -> str:
    """Resolve image for one-shot runner.

    May read mmp_core_api's image name for dependency parity, but NEVER
    executes alembic inside mmp_core_api.
    """
    explicit = (os.environ.get("L4_ALEMBIC_RUNNER_IMAGE") or "").strip()
    if explicit:
        return explicit
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.Config.Image}}", "mmp_core_api"],
        capture_output=True,
        text=True,
        check=False,
    )
    image = (r.stdout or "").strip()
    if not image:
        raise RuntimeError(
            "REFUSE: cannot resolve L4 alembic runner image. "
            "Set L4_ALEMBIC_RUNNER_IMAGE explicitly."
        )
    return image


def _alembic(db: str, *args: str) -> None:
    """Run alembic in disposable mmp_l4_alembic_runner against allowlisted DB only."""
    name = assert_rehearsal_db_name(db)
    database_url = rehearsal_database_url(name)
    verified = assert_database_url_is_rehearsal(database_url)
    print(f"event=alembic_target_verify db={verified} url_db={verified}")
    print(f"event=alembic_refuse_production refused_db={PRODUCTION_DB}")

    if ALEMBIC_RUNNER_NAME == "mmp_core_api":
        raise RuntimeError("REFUSE: alembic runner name must not be mmp_core_api")

    image = _resolve_alembic_runner_image()
    # Ensure any prior crashed runner with the same name is gone.
    subprocess.run(
        ["docker", "rm", "-f", ALEMBIC_RUNNER_NAME],
        capture_output=True,
        text=True,
        check=False,
    )

    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        ALEMBIC_RUNNER_NAME,
        "--network",
        COMPOSE_NETWORK,
        # Host source tree — latest local alembic revisions; no docker cp to core_api.
        "-v",
        f"{PROJECT_ROOT}:/app:ro",
        "-e",
        f"DATABASE_URL={database_url}",
        # Explicitly unset Redis / worker / send flags — rehearsal has no messaging.
        "-e",
        "REDIS_URL=",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "RUBIKA_CANONICAL_SESSION_V1=false",
        "-e",
        "REAL_MESSAGE_SENDING_ENABLED=false",
        "-e",
        "WORKER_EXECUTION_ENABLED=false",
        "-e",
        "CHANNEL_CONNECTORS_ENABLED=false",
        "-w",
        "/app",
        image,
        "alembic",
        *args,
    ]
    # Hard assert: never docker-exec into production API for alembic.
    joined = " ".join(cmd)
    if "docker exec" in joined or "mmp_core_api" in cmd:
        raise RuntimeError("REFUSE: mmp_core_api / docker exec must not run alembic")

    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        raise RuntimeError(f"alembic failed on db={name}: {' '.join(args)}")


def _inspect_canonical_loader(db: str, account_id: int) -> str:
    """Read-only canonical loader probe via isolated runner (not mmp_core_api)."""
    name = assert_rehearsal_db_name(db)
    database_url = rehearsal_database_url(name)
    assert_database_url_is_rehearsal(database_url)
    code = f"""
import os
os.environ['DATABASE_URL'] = {database_url!r}
from core_engine.database import SessionLocal
from core_engine.services.rubika_canonical_session import (
    CanonicalSessionError,
    load_canonical_rubika_session,
)
db = SessionLocal()
try:
    try:
        load_canonical_rubika_session(db, {int(account_id)})
        print('UNEXPECTED_ACTIVE')
    except CanonicalSessionError as exc:
        print(exc.code)
finally:
    db.close()
"""
    image = _resolve_alembic_runner_image()
    subprocess.run(
        ["docker", "rm", "-f", ALEMBIC_RUNNER_NAME],
        capture_output=True,
        text=True,
        check=False,
    )
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        ALEMBIC_RUNNER_NAME,
        "--network",
        COMPOSE_NETWORK,
        "-v",
        f"{PROJECT_ROOT}:/app:ro",
        "-e",
        f"DATABASE_URL={database_url}",
        "-e",
        "REDIS_URL=",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "RUBIKA_CANONICAL_SESSION_V1=false",
        "-w",
        "/app",
        image,
        "python",
        "-c",
        code,
    ]
    if "exec" in cmd and "mmp_core_api" in cmd:
        raise RuntimeError("REFUSE: mmp_core_api must not run inspection")
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return (r.stdout or r.stderr or "ERROR").strip().splitlines()[-1]


# ---------------------------------------------------------------------------
# Inventory helpers (rehearsal DB only)
# ---------------------------------------------------------------------------


def _table_exists(db: str, table: str) -> bool:
    assert_rehearsal_db_name(db)
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", table):
        raise RuntimeError(f"REFUSE: invalid table name {table!r}")
    return _docker_psql(db, f"SELECT to_regclass('{table}') IS NOT NULL;") == "t"


def _column_exists(db: str, table: str, column: str) -> bool:
    assert_rehearsal_db_name(db)
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", table):
        raise RuntimeError(f"REFUSE: invalid table name {table!r}")
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", column):
        raise RuntimeError(f"REFUSE: invalid column name {column!r}")
    return (
        _docker_psql(
            db,
            "SELECT COUNT(*) FROM information_schema.columns "
            f"WHERE table_name='{table}' AND column_name='{column}';",
        )
        != "0"
    )


def _counts(db: str) -> dict:
    assert_rehearsal_db_name(db)

    def q(sql: str) -> int:
        return int(_docker_psql(db, sql) or "0")

    rubika_accounts = q(
        "SELECT COUNT(*) FROM accounts WHERE platform::text IN ('rubika','RUBIKA');"
    )
    sessions_total = q("SELECT COUNT(*) FROM channel_sessions;")
    rubika_sessions = q(
        "SELECT COUNT(*) FROM channel_sessions "
        "WHERE session_type::text IN ('rubika_session','RUBIKA_SESSION');"
    )
    has_status = _column_exists(db, "channel_sessions", "session_status")
    active = (
        q(
            "SELECT COUNT(*) FROM channel_sessions WHERE session_status::text = 'active' "
            "AND session_type::text IN ('rubika_session','RUBIKA_SESSION');"
        )
        if has_status
        else 0
    )
    legacy = (
        q(
            "SELECT COUNT(*) FROM channel_sessions "
            "WHERE session_status::text = 'legacy_unclassified' "
            "AND session_type::text IN ('rubika_session','RUBIKA_SESSION');"
        )
        if has_status
        else None
    )
    challenges = (
        q("SELECT COUNT(*) FROM rubika_login_challenges;")
        if _table_exists(db, "rubika_login_challenges")
        else 0
    )
    return {
        "rubika_accounts": rubika_accounts,
        "channel_sessions_total": sessions_total,
        "rubika_sessions": rubika_sessions,
        "rubika_active": active,
        "rubika_legacy_unclassified": legacy,
        "login_challenges": challenges,
        "has_session_status_column": has_status,
    }


def _account_session_ids(db: str, account_id: int) -> list[int]:
    assert_rehearsal_db_name(db)
    raw = _docker_psql(
        db,
        f"SELECT id FROM channel_sessions WHERE account_id={int(account_id)} "
        f"AND session_type::text IN ('rubika_session','RUBIKA_SESSION') ORDER BY id;",
    )
    return [int(x) for x in raw.split() if x.strip()]


def _duplicate_accounts(db: str) -> dict[int, int]:
    assert_rehearsal_db_name(db)
    raw = _docker_psql(
        db,
        """
        SELECT account_id, COUNT(*) FROM channel_sessions
        WHERE session_type::text IN ('rubika_session','RUBIKA_SESSION')
        GROUP BY account_id HAVING COUNT(*) > 1 ORDER BY account_id;
        """,
    )
    out: dict[int, int] = {}
    for line in raw.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 2:
            out[int(parts[0].strip())] = int(parts[1].strip())
    return out


# ---------------------------------------------------------------------------
# Main rehearsal (operator-invoked only — not auto-run from this hardening step)
# ---------------------------------------------------------------------------


def main() -> int:
    # Connectivity check only — no writes to production.
    if _docker_psql(PRODUCTION_DB, "SELECT 1;") != "1":
        raise SystemExit("REFUSE: production database not reachable for read-only dump")

    # Exact-name stale reset before any restore/migrate.
    reset_stale_rehearsal_databases()

    dump_path = _backup_production()
    _restore(dump_path, REHEARSAL_DB)

    before = _counts(REHEARSAL_DB)
    acct12_before = _account_session_ids(REHEARSAL_DB, 12)
    acct79_before = _account_session_ids(REHEARSAL_DB, 79)
    dups_before = _duplicate_accounts(REHEARSAL_DB)

    _alembic(REHEARSAL_DB, "upgrade", "rubika_l2_canonical_session_001")
    after_l2 = _counts(REHEARSAL_DB)
    acct12_l2 = _account_session_ids(REHEARSAL_DB, 12)
    acct79_l2 = _account_session_ids(REHEARSAL_DB, 79)
    dups_l2 = _duplicate_accounts(REHEARSAL_DB)

    l2_ok = (
        after_l2["rubika_accounts"] == before["rubika_accounts"]
        and after_l2["rubika_sessions"] == before["rubika_sessions"]
        and after_l2["rubika_active"] == 0
        and after_l2["rubika_legacy_unclassified"] == before["rubika_sessions"]
        and acct12_l2 == acct12_before
        and acct79_l2 == acct79_before
        and dups_l2 == dups_before
        and before.get("has_session_status_column") is False
    )

    _alembic(REHEARSAL_DB, "upgrade", "rubika_l3_login_challenge_001")
    after_l3 = _counts(REHEARSAL_DB)
    l3_ok = (
        _table_exists(REHEARSAL_DB, "rubika_login_challenges")
        and after_l3["login_challenges"] == 0
        and after_l3["rubika_sessions"] == before["rubika_sessions"]
        and after_l3["rubika_active"] == 0
    )

    canonical_samples = {
        str(aid): _inspect_canonical_loader(REHEARSAL_DB, aid) for aid in (12, 79, 1, 2)
    }

    # Downgrade on disposable clone (exact allowlist only).
    _restore(dump_path, DOWNGRADE_DB)
    _alembic(DOWNGRADE_DB, "upgrade", "rubika_l2_canonical_session_001")
    _alembic(DOWNGRADE_DB, "upgrade", "rubika_l3_login_challenge_001")
    downgrade_ok = True
    downgrade_err = ""
    try:
        _alembic(DOWNGRADE_DB, "downgrade", "rubika_l2_canonical_session_001")
        l3_table_gone = not _table_exists(DOWNGRADE_DB, "rubika_login_challenges")
        _alembic(DOWNGRADE_DB, "downgrade", "campaign_accounts_001")
        l2_cols_gone = not _column_exists(DOWNGRADE_DB, "channel_sessions", "session_status")
        downgrade_ok = l3_table_gone and l2_cols_gone
    except Exception as exc:
        downgrade_ok = False
        downgrade_err = str(exc)

    artifact = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "production_db": PRODUCTION_DB,
        "rehearsal_db": REHEARSAL_DB,
        "downgrade_db": DOWNGRADE_DB,
        "backup": str(dump_path),
        "alembic_runner": ALEMBIC_RUNNER_NAME,
        "alembic_runner_uses_core_api": False,
        "docker_cp_to_core_api": False,
        "counts_before": before,
        "counts_after_l2": after_l2,
        "counts_after_l3": after_l3,
        "account12_session_ids_before": acct12_before,
        "account12_session_ids_after_l2": acct12_l2,
        "account79_session_ids_before": acct79_before,
        "account79_session_ids_after_l2": acct79_l2,
        "duplicates_before": dups_before,
        "duplicates_after_l2": dups_l2,
        "l2_rehearsal_pass": l2_ok,
        "l3_rehearsal_pass": l3_ok,
        "canonical_loader_samples": canonical_samples,
        "downgrade_rehearsal_pass": downgrade_ok,
        "downgrade_error": downgrade_err,
        "production_migration_applied": False,
    }
    ARTIFACT.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps(artifact, indent=2))
    return 0 if l2_ok and l3_ok and downgrade_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
