#!/usr/bin/env python3
"""L5 production schema apply wrapper — HARDENED positive-authorization gate.

Does NOT run automatically. Operator must set ALL of:

  ALLOW_PRODUCTION_SCHEMA_MIGRATION=1
  FRESH_BACKUP_PATH=<absolute path to verified pg_dump>
  FRESH_BACKUP_SHA256=<sha256 hex of that dump>

Companion backup metadata JSON (same path + ".meta.json" OR FRESH_BACKUP_META_PATH)
must prove source_db=mmp_db and freshness <= BACKUP_MAX_AGE_MINUTES (default 30).

Steps:
  L5_APPLY_STEP=backup|precheck|l2|verify_l2|l3|verify_post|downgrade_l3|downgrade_l2|refresh_plan_hashes|help

L2 flow (non-bypassable):
  authorize → backup verify → source hash lock → precheck → L2 apply → verify_l2 → STOP

L3 is a separate step and re-verifies L2 + still-valid safety gates first.
Post-L3 verification uses verify_l3/verify_post (expects rubika_l3_login_challenge_001),
NOT verify_l2's alembic_version assertion.

RUNBOOK: reports/rubika-remediation/L5_PRODUCTION_SCHEMA_APPLY_RUNBOOK.md
PLAN: reports/rubika-remediation/L5_PRODUCTION_SCHEMA_APPLY_PLAN.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PRODUCTION_DB = "mmp_db"
PG_USER = os.environ.get("PGUSER", "mmp_user")
PG_PASSWORD = os.environ.get("PGPASSWORD", "mmp_pass")
PG_HOST_CONTAINER = os.environ.get("PGHOST", "mmp_postgres")
PG_SERVICE_HOSTNAME = os.environ.get("L5_PG_SERVICE_HOSTNAME", "postgres")
COMPOSE_NETWORK = os.environ.get("L5_MIGRATION_NETWORK", "multi-messaging-platform_default")
RUNNER_NAME = "mmp_l5_production_alembic_runner"
REDIS_CONTAINER = os.environ.get("L5_REDIS_CONTAINER", "mmp_redis")
CORE_API_CONTAINER = os.environ.get("L5_CORE_API_CONTAINER", "mmp_core_api")
WORKER_CONTAINER = os.environ.get("L5_RUBIKA_WORKER_CONTAINER", "mmp_rubika_worker")

BACKUP_MAX_AGE_MINUTES = int(os.environ.get("L5_BACKUP_MAX_AGE_MINUTES", "30"))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = PROJECT_ROOT / "reports" / "rubika-remediation" / "L5_PRODUCTION_SCHEMA_APPLY_PLAN.json"
REPORT_DIR = PROJECT_ROOT / "reports" / "rubika-remediation" / "l5_production_apply"
ARTIFACT = REPORT_DIR / "L5_PRODUCTION_SCHEMA_APPLY_EXECUTION.json"
BASELINE_PATH = REPORT_DIR / "L5_PRECHECK_BASELINE.json"

REVISION_L2 = "rubika_l2_canonical_session_001"
REVISION_L3 = "rubika_l3_login_challenge_001"
REVISION_CURRENT = "campaign_accounts_001"

EXPECTED_ACCOUNT12 = frozenset({657, 728})
EXPECTED_ACCOUNT79 = frozenset({600})
EXPECTED_SESSION_OWNERS = {600: 79, 657: 12, 728: 12}
PINNED_ACCOUNT_IDS = frozenset({12, 79})

SOURCE_REL_PATHS = {
    "rubika_l2_canonical_session_001.py": PROJECT_ROOT
    / "alembic"
    / "versions"
    / "rubika_l2_canonical_session_001.py",
    "rubika_l3_login_challenge_001.py": PROJECT_ROOT
    / "alembic"
    / "versions"
    / "rubika_l3_login_challenge_001.py",
    "scripts/_l5_production_schema_apply.py": PROJECT_ROOT
    / "scripts"
    / "_l5_production_schema_apply.py",
}


class GateFailure(RuntimeError):
    """Hard stop with structured rollback recommendation."""

    def __init__(
        self,
        *,
        failed_stage: str,
        failed_gate: str,
        expected: Any,
        actual: Any,
        rollback_recommendation: str,
        message: str = "",
    ) -> None:
        self.failed_stage = failed_stage
        self.failed_gate = failed_gate
        self.expected = expected
        self.actual = actual
        self.rollback_recommendation = rollback_recommendation
        detail = message or f"{failed_gate}: expected={expected!r} actual={actual!r}"
        super().__init__(detail)

    def as_dict(self) -> dict[str, Any]:
        return {
            "FAILED_STAGE": self.failed_stage,
            "FAILED_GATE": self.failed_gate,
            "EXPECTED": self.expected,
            "ACTUAL": self.actual,
            "ROLLBACK_RECOMMENDATION": self.rollback_recommendation,
            "message": str(self),
        }


@dataclass
class PrecheckBaseline:
    alembic_version: str
    total_rubika_accounts: int
    total_channel_session_count: int
    account12_session_ids: list[int]
    account79_session_ids: list[int]
    session_owners: dict[str, int]
    duplicate_account_ids: list[int]
    captured_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().lower()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def assert_production_migration_authorized() -> None:
    if os.environ.get("ALLOW_PRODUCTION_SCHEMA_MIGRATION") != "1":
        raise GateFailure(
            failed_stage="AUTHORIZE",
            failed_gate="ALLOW_PRODUCTION_SCHEMA_MIGRATION",
            expected="1",
            actual=os.environ.get("ALLOW_PRODUCTION_SCHEMA_MIGRATION"),
            rollback_recommendation="NO_MIGRATION_OCCURRED",
            message="REFUSE: set ALLOW_PRODUCTION_SCHEMA_MIGRATION=1",
        )


def assert_production_db_name(db_name: str) -> str:
    name = str(db_name or "").strip()
    if name != PRODUCTION_DB:
        raise GateFailure(
            failed_stage="TARGET_DB",
            failed_gate="TARGET_DB_FIXED_TO_MMP_DB",
            expected=PRODUCTION_DB,
            actual=name,
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    return name


def production_database_url() -> str:
    assert_production_db_name(PRODUCTION_DB)
    return (
        f"postgresql://{PG_USER}:{PG_PASSWORD}"
        f"@{PG_SERVICE_HOSTNAME}:5432/{PRODUCTION_DB}"
    )


def assert_database_url_is_production(database_url: str) -> str:
    parsed = urlparse(database_url)
    db_name = (parsed.path or "").lstrip("/").split("?")[0].strip()
    return assert_production_db_name(db_name)


# ---------------------------------------------------------------------------
# Backup gate
# ---------------------------------------------------------------------------


def _backup_meta_path(backup_path: Path) -> Path:
    explicit = (os.environ.get("FRESH_BACKUP_META_PATH") or "").strip()
    if explicit:
        return Path(explicit)
    return Path(str(backup_path) + ".meta.json")


def verify_fresh_backup(*, stage: str = "BACKUP") -> dict[str, Any]:
    backup_path = (os.environ.get("FRESH_BACKUP_PATH") or "").strip()
    expected_hash = (os.environ.get("FRESH_BACKUP_SHA256") or "").strip().lower()
    if not backup_path or not expected_hash:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="FRESH_BACKUP_ENV",
            expected="FRESH_BACKUP_PATH + FRESH_BACKUP_SHA256",
            actual={"path": backup_path or None, "sha256": bool(expected_hash)},
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    p = Path(backup_path)
    if not p.is_file():
        raise GateFailure(
            failed_stage=stage,
            failed_gate="BACKUP_FILE_EXISTS",
            expected=str(p),
            actual="missing",
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    size = p.stat().st_size
    if size <= 0:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="BACKUP_FILE_NONEMPTY",
            expected="size > 0",
            actual=size,
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    digest = _sha256_file(p)
    if digest != expected_hash:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="BACKUP_SHA256",
            expected=expected_hash,
            actual=digest,
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )

    meta_path = _backup_meta_path(p)
    if not meta_path.is_file():
        raise GateFailure(
            failed_stage=stage,
            failed_gate="BACKUP_META_EXISTS",
            expected=str(meta_path),
            actual="missing",
            rollback_recommendation="NO_MIGRATION_OCCURRED",
            message="Companion backup metadata JSON required (source_db/created_at/sha256/size)",
        )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if str(meta.get("source_db") or "") != PRODUCTION_DB:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="BACKUP_PROVENANCE",
            expected=PRODUCTION_DB,
            actual=meta.get("source_db"),
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    if str(meta.get("sha256") or "").lower() != digest:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="BACKUP_META_SHA256",
            expected=digest,
            actual=meta.get("sha256"),
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    meta_size = int(meta.get("size_bytes") or 0)
    if meta_size != size:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="BACKUP_META_SIZE",
            expected=size,
            actual=meta_size,
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    created_raw = str(meta.get("created_at") or "")
    if not created_raw:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="BACKUP_CREATED_AT",
            expected="ISO8601 created_at in meta",
            actual=None,
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    created = _parse_iso(created_raw)
    age_sec = (_now() - created).total_seconds()
    max_age = BACKUP_MAX_AGE_MINUTES * 60
    if age_sec < 0 or age_sec > max_age:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="BACKUP_FRESHNESS",
            expected=f"age_seconds <= {max_age}",
            actual={"age_seconds": age_sec, "created_at": created_raw},
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    return {
        "backup_path": str(p),
        "sha256": digest,
        "size_bytes": size,
        "meta_path": str(meta_path),
        "source_db": meta.get("source_db"),
        "created_at": created_raw,
        "age_seconds": age_sec,
    }


def create_fresh_backup() -> dict[str, Any]:
    """Operator backup step — writes dump + provenance metadata JSON."""
    assert_production_migration_authorized()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = _now().strftime("%Y%m%dT%H%M%SZ")
    backup_dir = REPORT_DIR / "backups" / ts
    backup_dir.mkdir(parents=True, exist_ok=True)
    dump_name = f"mmp_db_pre_l5_{ts}.dump"
    dump_path = backup_dir / dump_name
    container_dump = f"/tmp/{dump_name}"
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
            container_dump,
            PRODUCTION_DB,
        ]
    )
    _run(["docker", "cp", f"{PG_HOST_CONTAINER}:{container_dump}", str(dump_path)])
    digest = _sha256_file(dump_path)
    size = dump_path.stat().st_size
    if size <= 0:
        raise GateFailure(
            failed_stage="BACKUP",
            failed_gate="BACKUP_FILE_NONEMPTY",
            expected="size > 0",
            actual=size,
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    meta = {
        "source_db": PRODUCTION_DB,
        "created_at": _now().isoformat(),
        "backup_path": str(dump_path),
        "sha256": digest,
        "size_bytes": size,
        "mutation": False,
        "mode": "pg_dump_read_only",
    }
    meta_path = Path(str(dump_path) + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"BACKUP_PATH={dump_path}")
    print(f"BACKUP_SHA256={digest}")
    print(f"BACKUP_META={meta_path}")
    print("Set FRESH_BACKUP_PATH / FRESH_BACKUP_SHA256 to these values before l2/l3.")
    return meta


# ---------------------------------------------------------------------------
# Source hash lock
# ---------------------------------------------------------------------------


def assert_source_hashes_match_plan(*, stage: str = "SOURCE_HASH") -> dict[str, str]:
    if not PLAN_PATH.is_file():
        raise GateFailure(
            failed_stage=stage,
            failed_gate="L5_PLAN_EXISTS",
            expected=str(PLAN_PATH),
            actual="missing",
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    expected = plan.get("migration_source_hashes") or {}
    actual: dict[str, str] = {}
    for key, path in SOURCE_REL_PATHS.items():
        if not path.is_file():
            raise GateFailure(
                failed_stage=stage,
                failed_gate="SOURCE_FILE_EXISTS",
                expected=str(path),
                actual="missing",
                rollback_recommendation="NO_MIGRATION_OCCURRED",
            )
        digest = _sha256_file(path)
        actual[key] = digest
        exp = str(expected.get(key) or "").lower()
        if not exp:
            raise GateFailure(
                failed_stage=stage,
                failed_gate="SOURCE_HASH_PLAN_ENTRY",
                expected=f"plan.migration_source_hashes[{key}]",
                actual=None,
                rollback_recommendation="NO_MIGRATION_OCCURRED",
                message="Run L5_APPLY_STEP=refresh_plan_hashes after explicit operator approval",
            )
        if digest != exp:
            raise GateFailure(
                failed_stage=stage,
                failed_gate="SOURCE_HASH_LOCK",
                expected=exp,
                actual=digest,
                rollback_recommendation="NO_MIGRATION_OCCURRED",
                message=f"Source changed since plan approval: {key}",
            )
    return actual


def refresh_plan_hashes(*, reason: str) -> dict[str, Any]:
    """Explicit plan-refresh only — does not apply migrations."""
    assert_production_migration_authorized()
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    new_hashes = {k: _sha256_file(p) for k, p in SOURCE_REL_PATHS.items()}
    old = dict(plan.get("migration_source_hashes") or {})
    plan["migration_source_hashes"] = new_hashes
    plan["migration_source_hash_refresh"] = {
        "refreshed_at": _now().isoformat(),
        "reason": reason,
        "previous": old,
        "current": new_hashes,
    }
    PLAN_PATH.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return plan["migration_source_hash_refresh"]


# ---------------------------------------------------------------------------
# DB / Redis / env helpers
# ---------------------------------------------------------------------------


def _docker_psql(sql: str) -> str:
    r = _run(
        [
            "docker",
            "exec",
            PG_HOST_CONTAINER,
            "psql",
            "-U",
            PG_USER,
            "-d",
            PRODUCTION_DB,
            "-tAc",
            sql,
        ]
    )
    return (r.stdout or "").strip()


def _parse_id_set(raw: str) -> set[int]:
    return {int(x) for x in raw.replace("|", " ").split() if x.strip().isdigit()}


def _redis_password() -> str:
    secret = PROJECT_ROOT / "secrets" / "redis_password.txt"
    if secret.is_file():
        return secret.read_text(encoding="utf-8").strip()
    env_pw = (os.environ.get("REDIS_PASSWORD") or "").strip()
    if env_pw:
        return env_pw
    raise GateFailure(
        failed_stage="PRECHECK",
        failed_gate="REDIS_PASSWORD",
        expected="secrets/redis_password.txt or REDIS_PASSWORD",
        actual="missing",
        rollback_recommendation="NO_MIGRATION_OCCURRED",
    )


def _redis_llen(key: str) -> int:
    pw = _redis_password()
    r = _run(
        ["docker", "exec", REDIS_CONTAINER, "redis-cli", "-a", pw, "--no-auth-warning", "LLEN", key],
        check=False,
    )
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out.isdigit():
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="REDIS_LLEN",
            expected="numeric length",
            actual={"key": key, "stdout": out, "stderr": (r.stderr or "")[:200]},
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    return int(out)


def _container_env(container: str) -> dict[str, str]:
    r = _run(
        ["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", container],
        check=False,
    )
    env: dict[str, str] = {}
    for line in (r.stdout or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    return env


def _env_bool_false(value: str | None) -> bool:
    if value is None or value.strip() == "":
        return True  # unset → app default false for these flags
    return value.strip().lower() in {"0", "false", "no", "off"}


def _pinned_account_ids(raw: str | None) -> set[int]:
    if not raw:
        return set()
    return {int(x) for x in re.split(r"[,\s]+", raw.strip()) if x.isdigit()}


# ---------------------------------------------------------------------------
# Precheck
# ---------------------------------------------------------------------------


def precheck(*, stage: str = "PRECHECK") -> PrecheckBaseline:
    assert_production_migration_authorized()
    verify_fresh_backup(stage=stage)
    assert_source_hashes_match_plan(stage=stage)

    version = _docker_psql("SELECT version_num FROM alembic_version;")
    if version != REVISION_CURRENT:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="ALEMBIC_VERSION",
            expected=REVISION_CURRENT,
            actual=version,
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )

    rubika_n = int(
        _docker_psql(
            "SELECT COUNT(*) FROM accounts WHERE platform::text IN ('rubika','RUBIKA');"
        )
    )
    if rubika_n != 43:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="TOTAL_RUBIKA_ACCOUNTS",
            expected=43,
            actual=rubika_n,
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )

    a12 = _parse_id_set(
        _docker_psql(
            "SELECT id FROM channel_sessions WHERE account_id=12 "
            "AND session_type::text IN ('rubika_session','RUBIKA_SESSION') ORDER BY id;"
        )
    )
    a79 = _parse_id_set(
        _docker_psql(
            "SELECT id FROM channel_sessions WHERE account_id=79 "
            "AND session_type::text IN ('rubika_session','RUBIKA_SESSION') ORDER BY id;"
        )
    )
    if a12 != EXPECTED_ACCOUNT12:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="ACCOUNT12_SESSION_IDS",
            expected=sorted(EXPECTED_ACCOUNT12),
            actual=sorted(a12),
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    if a79 != EXPECTED_ACCOUNT79:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="ACCOUNT79_SESSION_IDS",
            expected=sorted(EXPECTED_ACCOUNT79),
            actual=sorted(a79),
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )

    owners_raw = _docker_psql(
        "SELECT id || ':' || account_id FROM channel_sessions "
        "WHERE id IN (600,657,728) ORDER BY id;"
    )
    owners: dict[int, int] = {}
    for token in owners_raw.split():
        sid_s, aid_s = token.split(":")
        owners[int(sid_s)] = int(aid_s)
    if owners != EXPECTED_SESSION_OWNERS:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="SESSION_OWNERSHIP",
            expected=EXPECTED_SESSION_OWNERS,
            actual=owners,
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )

    status_col = _docker_psql(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_name='channel_sessions' AND column_name='session_status';"
    )
    if status_col != "0":
        raise GateFailure(
            failed_stage=stage,
            failed_gate="SESSION_STATUS_ABSENT",
            expected="0",
            actual=status_col,
            rollback_recommendation="NO_MIGRATION_OCCURRED",
            message="session_status already present — refuse L2 re-apply",
        )

    camps = _docker_psql(
        "SELECT id || ':' || status::text FROM campaigns WHERE id IN (101,102) ORDER BY id;"
    )
    camp_map = {}
    for token in camps.split():
        cid, st = token.split(":", 1)
        camp_map[int(cid)] = st
    if camp_map.get(101) != "paused" or camp_map.get(102) != "paused":
        raise GateFailure(
            failed_stage=stage,
            failed_gate="CAMPAIGNS_PAUSED",
            expected={101: "paused", 102: "paused"},
            actual=camp_map,
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )

    active_remediation = _docker_psql(
        "SELECT COUNT(*) FROM campaigns WHERE status::text NOT IN "
        "('paused','completed','cancelled','failed') "
        "AND (name ILIKE '%R10%' OR name ILIKE '%remediat%' OR name ILIKE '%L5%' "
        "OR name ILIKE '%test%send%');"
    )
    if int(active_remediation or "0") != 0:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="NO_RUNNING_REMEDIATION_CAMPAIGN",
            expected=0,
            actual=active_remediation,
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )

    q12 = _redis_llen("queue:rubika:12")
    q79 = _redis_llen("queue:rubika:79")
    if q12 != 0 or q79 != 0:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="QUEUES_EMPTY",
            expected={"queue12": 0, "queue79": 0},
            actual={"queue12": q12, "queue79": q79},
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )

    api_env = _container_env(CORE_API_CONTAINER)
    worker_env = _container_env(WORKER_CONTAINER)
    flag_api = api_env.get("RUBIKA_CANONICAL_SESSION_V1")
    flag_worker = worker_env.get("RUBIKA_CANONICAL_SESSION_V1")
    if not _env_bool_false(flag_api) or not _env_bool_false(flag_worker):
        raise GateFailure(
            failed_stage=stage,
            failed_gate="RUBIKA_CANONICAL_SESSION_V1",
            expected="false/unset",
            actual={"core_api": flag_api, "rubika_worker": flag_worker},
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    enroll_api = api_env.get("AUTO_ENROLL_RUBIKA_POOL")
    enroll_worker = worker_env.get("AUTO_ENROLL_RUBIKA_POOL")
    if not _env_bool_false(enroll_api) or not _env_bool_false(enroll_worker):
        raise GateFailure(
            failed_stage=stage,
            failed_gate="AUTO_ENROLL_RUBIKA_POOL",
            expected="false/unset",
            actual={"core_api": enroll_api, "rubika_worker": enroll_worker},
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    pinned = _pinned_account_ids(worker_env.get("RUBIKA_ACCOUNT_IDS"))
    if pinned != PINNED_ACCOUNT_IDS:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="RUBIKA_ACCOUNT_IDS",
            expected=sorted(PINNED_ACCOUNT_IDS),
            actual=sorted(pinned),
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )

    total_sessions = int(_docker_psql("SELECT COUNT(*) FROM channel_sessions;"))
    dups_raw = _docker_psql(
        "SELECT account_id FROM channel_sessions "
        "WHERE session_type::text IN ('rubika_session','RUBIKA_SESSION') "
        "GROUP BY account_id HAVING COUNT(*) > 1 ORDER BY account_id;"
    )
    dup_ids = sorted(_parse_id_set(dups_raw))

    baseline = PrecheckBaseline(
        alembic_version=version,
        total_rubika_accounts=rubika_n,
        total_channel_session_count=total_sessions,
        account12_session_ids=sorted(a12),
        account79_session_ids=sorted(a79),
        session_owners={str(k): v for k, v in owners.items()},
        duplicate_account_ids=dup_ids,
    )
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(asdict(baseline), indent=2), encoding="utf-8")
    return baseline


def _load_baseline() -> PrecheckBaseline:
    if not BASELINE_PATH.is_file():
        raise GateFailure(
            failed_stage="BASELINE",
            failed_gate="PRECHECK_BASELINE_MISSING",
            expected=str(BASELINE_PATH),
            actual="missing",
            rollback_recommendation="STOP_AT_L2_INVESTIGATE",
            message="Run precheck or l2 (which runs precheck) before verify",
        )
    data = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return PrecheckBaseline(**data)


# ---------------------------------------------------------------------------
# Alembic runner
# ---------------------------------------------------------------------------


def _resolve_runner_image() -> str:
    explicit = (os.environ.get("L5_ALEMBIC_RUNNER_IMAGE") or "").strip()
    if explicit:
        return explicit
    r = _run(
        ["docker", "inspect", "-f", "{{.Config.Image}}", CORE_API_CONTAINER],
        check=False,
    )
    image = (r.stdout or "").strip()
    if not image:
        raise GateFailure(
            failed_stage="RUNNER",
            failed_gate="ALEMBIC_RUNNER_IMAGE",
            expected="image name",
            actual=None,
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    return image


def _alembic(*args: str, stage: str) -> None:
    assert_production_migration_authorized()
    database_url = production_database_url()
    db_name = assert_database_url_is_production(database_url)
    print(f"event=alembic_production_target db={db_name} TARGET_DB={db_name}")
    if "head" in args:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="EXPLICIT_REVISION_ONLY",
            expected="explicit revision id",
            actual=list(args),
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    image = _resolve_runner_image()
    subprocess.run(["docker", "rm", "-f", RUNNER_NAME], capture_output=True, check=False)
    cmd = [
        "docker",
        "run",
        "--rm",
        "--name",
        RUNNER_NAME,
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
        "-e",
        "AUTO_ENROLL_RUBIKA_POOL=false",
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
    if cmd[0:2] == ["docker", "exec"] or (
        "mmp_core_api" in cmd and cmd[cmd.index("mmp_core_api") - 1] == "exec"
        if "mmp_core_api" in cmd
        else False
    ):
        raise GateFailure(
            failed_stage=stage,
            failed_gate="CORE_API_NOT_RUNNER",
            expected="docker run mmp_l5_production_alembic_runner",
            actual="docker exec mmp_core_api",
            rollback_recommendation="NO_MIGRATION_OCCURRED",
        )
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        raise GateFailure(
            failed_stage=stage,
            failed_gate="ALEMBIC_COMMAND",
            expected="exit 0",
            actual={"args": list(args), "stderr": (r.stderr or "")[-500:]},
            rollback_recommendation=(
                "DOWNGRADE_L2_TO_CAMPAIGN_ACCOUNTS"
                if stage == "L2"
                else "DOWNGRADE_L3_TO_L2"
                if stage == "L3"
                else "STOP_AT_L2_INVESTIGATE"
            ),
        )


# ---------------------------------------------------------------------------
# verify_l2 / verify_post
# ---------------------------------------------------------------------------


def _assert_post_migration_session_invariants(
    *,
    stage: str,
    baseline: PrecheckBaseline,
    rollback_on_schema: str,
) -> dict[str, Any]:
    """Session/account invariants shared by L2 and L3 post-checks.

    Does NOT assert alembic_version — callers enforce the revision for their stage.
    """
    status_col = _docker_psql(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_name='channel_sessions' AND column_name='session_status';"
    )
    if status_col != "1":
        raise GateFailure(
            failed_stage=stage,
            failed_gate="SESSION_STATUS_COLUMN",
            expected="1",
            actual=status_col,
            rollback_recommendation=rollback_on_schema,
        )

    total = int(_docker_psql("SELECT COUNT(*) FROM channel_sessions;"))
    if total != baseline.total_channel_session_count:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="TOTAL_CHANNEL_SESSION_COUNT",
            expected=baseline.total_channel_session_count,
            actual=total,
            rollback_recommendation="FULL_RESTORE_FROM_FRESH_BACKUP",
        )

    legacy = int(
        _docker_psql(
            "SELECT COUNT(*) FROM channel_sessions "
            "WHERE session_status::text='legacy_unclassified';"
        )
    )
    active = int(
        _docker_psql(
            "SELECT COUNT(*) FROM channel_sessions WHERE session_status::text='active';"
        )
    )
    if active != 0:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="ACTIVE_ZERO",
            expected=0,
            actual=active,
            rollback_recommendation=rollback_on_schema,
        )
    if legacy != total:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="ALL_LEGACY_UNCLASSIFIED",
            expected=total,
            actual=legacy,
            rollback_recommendation=rollback_on_schema,
        )

    a12 = _parse_id_set(
        _docker_psql(
            "SELECT id FROM channel_sessions WHERE account_id=12 "
            "AND session_type::text IN ('rubika_session','RUBIKA_SESSION') ORDER BY id;"
        )
    )
    a79 = _parse_id_set(
        _docker_psql(
            "SELECT id FROM channel_sessions WHERE account_id=79 "
            "AND session_type::text IN ('rubika_session','RUBIKA_SESSION') ORDER BY id;"
        )
    )
    if a12 != EXPECTED_ACCOUNT12 or a12 != set(baseline.account12_session_ids):
        raise GateFailure(
            failed_stage=stage,
            failed_gate="ACCOUNT12_SESSION_IDS",
            expected=sorted(EXPECTED_ACCOUNT12),
            actual=sorted(a12),
            rollback_recommendation="FULL_RESTORE_FROM_FRESH_BACKUP",
        )
    if a79 != EXPECTED_ACCOUNT79 or a79 != set(baseline.account79_session_ids):
        raise GateFailure(
            failed_stage=stage,
            failed_gate="ACCOUNT79_SESSION_IDS",
            expected=sorted(EXPECTED_ACCOUNT79),
            actual=sorted(a79),
            rollback_recommendation="FULL_RESTORE_FROM_FRESH_BACKUP",
        )

    owners_raw = _docker_psql(
        "SELECT id || ':' || account_id FROM channel_sessions "
        "WHERE id IN (600,657,728) ORDER BY id;"
    )
    owners: dict[int, int] = {}
    for token in owners_raw.split():
        sid_s, aid_s = token.split(":")
        owners[int(sid_s)] = int(aid_s)
    if owners != EXPECTED_SESSION_OWNERS:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="SESSION_OWNERSHIP",
            expected=EXPECTED_SESSION_OWNERS,
            actual=owners,
            rollback_recommendation="FULL_RESTORE_FROM_FRESH_BACKUP",
        )

    dups_raw = _docker_psql(
        "SELECT account_id FROM channel_sessions "
        "WHERE session_type::text IN ('rubika_session','RUBIKA_SESSION') "
        "GROUP BY account_id HAVING COUNT(*) > 1 ORDER BY account_id;"
    )
    dup_ids = sorted(_parse_id_set(dups_raw))
    if dup_ids != baseline.duplicate_account_ids:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="DUPLICATE_PRESERVATION",
            expected=baseline.duplicate_account_ids,
            actual=dup_ids,
            rollback_recommendation="FULL_RESTORE_FROM_FRESH_BACKUP",
        )

    rubika_n = int(
        _docker_psql(
            "SELECT COUNT(*) FROM accounts WHERE platform::text IN ('rubika','RUBIKA');"
        )
    )
    if rubika_n != 43:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="TOTAL_RUBIKA_ACCOUNTS",
            expected=43,
            actual=rubika_n,
            rollback_recommendation="FULL_RESTORE_FROM_FRESH_BACKUP",
        )

    return {
        "total_sessions": total,
        "legacy_unclassified": legacy,
        "active": active,
        "account12": sorted(a12),
        "account79": sorted(a79),
        "duplicates": dup_ids,
    }


def verify_l2(*, baseline: PrecheckBaseline | None = None) -> dict[str, Any]:
    """Post-L2 verifier — requires alembic_version == rubika_l2_canonical_session_001."""
    assert_production_migration_authorized()
    base = baseline or _load_baseline()
    version = _docker_psql("SELECT version_num FROM alembic_version;")
    if version != REVISION_L2:
        raise GateFailure(
            failed_stage="VERIFY_L2",
            failed_gate="ALEMBIC_VERSION",
            expected=REVISION_L2,
            actual=version,
            rollback_recommendation="DOWNGRADE_L2_TO_CAMPAIGN_ACCOUNTS",
        )
    invariants = _assert_post_migration_session_invariants(
        stage="VERIFY_L2",
        baseline=base,
        rollback_on_schema="DOWNGRADE_L2_TO_CAMPAIGN_ACCOUNTS",
    )
    return {"alembic_version": version, **invariants}


def verify_l3(*, baseline: PrecheckBaseline | None = None) -> dict[str, Any]:
    """Post-L3 verifier — requires alembic_version == rubika_l3_login_challenge_001."""
    assert_production_migration_authorized()
    base = baseline or _load_baseline()
    version = _docker_psql("SELECT version_num FROM alembic_version;")
    if version != REVISION_L3:
        raise GateFailure(
            failed_stage="VERIFY_L3",
            failed_gate="ALEMBIC_VERSION",
            expected=REVISION_L3,
            actual=version,
            rollback_recommendation="DOWNGRADE_L3_TO_L2",
        )
    invariants = _assert_post_migration_session_invariants(
        stage="VERIFY_L3",
        baseline=base,
        rollback_on_schema="DOWNGRADE_L3_TO_L2",
    )
    table = _docker_psql("SELECT to_regclass('rubika_login_challenges') IS NOT NULL;")
    if table != "t":
        raise GateFailure(
            failed_stage="VERIFY_L3",
            failed_gate="LOGIN_CHALLENGE_TABLE",
            expected="true",
            actual=table,
            rollback_recommendation="DOWNGRADE_L3_TO_L2",
        )
    challenges = int(_docker_psql("SELECT COUNT(*) FROM rubika_login_challenges;"))
    if challenges != 0:
        raise GateFailure(
            failed_stage="VERIFY_L3",
            failed_gate="CHALLENGE_ROW_COUNT",
            expected=0,
            actual=challenges,
            rollback_recommendation="DOWNGRADE_L3_TO_L2",
        )
    enum_n = int(
        _docker_psql(
            "SELECT COUNT(*) FROM pg_type WHERE typname='rubikaloginchallengestate';"
        )
    )
    if enum_n != 1:
        raise GateFailure(
            failed_stage="VERIFY_L3",
            failed_gate="ENUM_ONCE",
            expected=1,
            actual=enum_n,
            rollback_recommendation="DOWNGRADE_L3_TO_L2",
        )
    return {
        "alembic_version": version,
        "challenges": challenges,
        "enum_count": enum_n,
        **invariants,
    }


def verify_post() -> dict[str, Any]:
    """Alias for post-L3 verification (does NOT re-assert L2 alembic_version)."""
    return verify_l3()


def _still_valid_runtime_gates(*, stage: str) -> None:
    """Subset of prechecks that must hold before L3 (queues/campaigns/flags)."""
    camps = _docker_psql(
        "SELECT id || ':' || status::text FROM campaigns WHERE id IN (101,102) ORDER BY id;"
    )
    camp_map = {}
    for token in camps.split():
        cid, st = token.split(":", 1)
        camp_map[int(cid)] = st
    if camp_map.get(101) != "paused" or camp_map.get(102) != "paused":
        raise GateFailure(
            failed_stage=stage,
            failed_gate="CAMPAIGNS_PAUSED",
            expected={101: "paused", 102: "paused"},
            actual=camp_map,
            rollback_recommendation="STOP_AT_L2_INVESTIGATE",
        )
    q12 = _redis_llen("queue:rubika:12")
    q79 = _redis_llen("queue:rubika:79")
    if q12 != 0 or q79 != 0:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="QUEUES_EMPTY",
            expected={"queue12": 0, "queue79": 0},
            actual={"queue12": q12, "queue79": q79},
            rollback_recommendation="STOP_AT_L2_INVESTIGATE",
        )
    api_env = _container_env(CORE_API_CONTAINER)
    worker_env = _container_env(WORKER_CONTAINER)
    if not _env_bool_false(api_env.get("RUBIKA_CANONICAL_SESSION_V1")):
        raise GateFailure(
            failed_stage=stage,
            failed_gate="RUBIKA_CANONICAL_SESSION_V1",
            expected="false/unset",
            actual=api_env.get("RUBIKA_CANONICAL_SESSION_V1"),
            rollback_recommendation="STOP_AT_L2_INVESTIGATE",
        )
    pinned = _pinned_account_ids(worker_env.get("RUBIKA_ACCOUNT_IDS"))
    if pinned != PINNED_ACCOUNT_IDS:
        raise GateFailure(
            failed_stage=stage,
            failed_gate="RUBIKA_ACCOUNT_IDS",
            expected=sorted(PINNED_ACCOUNT_IDS),
            actual=sorted(pinned),
            rollback_recommendation="STOP_AT_L2_INVESTIGATE",
        )


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def apply_l2() -> dict[str, Any]:
    """authorize → backup → source hash → precheck → L2 → verify_l2 → STOP."""
    assert_production_migration_authorized()
    backup = verify_fresh_backup(stage="L2")
    hashes = assert_source_hashes_match_plan(stage="L2")
    baseline = precheck(stage="L2_PRECHECK")
    _alembic("upgrade", REVISION_L2, stage="L2")
    verification = verify_l2(baseline=baseline)
    return {
        "backup": backup,
        "source_hashes": hashes,
        "baseline": asdict(baseline),
        "verify_l2": verification,
        "STOP": True,
        "next": "Operator reviews L2 verification before any L5_APPLY_STEP=l3",
    }


def apply_l3() -> dict[str, Any]:
    assert_production_migration_authorized()
    verify_fresh_backup(stage="L3")
    assert_source_hashes_match_plan(stage="L3")
    verify_l2()  # hard stop if L2 not verified
    _still_valid_runtime_gates(stage="L3_PRECHECK")
    _alembic("upgrade", REVISION_L3, stage="L3")
    return {"verify_post": verify_post()}


def main() -> int:
    step = (os.environ.get("L5_APPLY_STEP") or "help").strip().lower()
    if step == "help":
        print(__doc__)
        return 0
    try:
        if step != "refresh_plan_hashes":
            assert_production_migration_authorized()
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        artifact: dict[str, Any] = {
            "step": step,
            "timestamp": _now().isoformat(),
            "production_apply_note": "manual step only",
        }
        if step == "backup":
            artifact["backup"] = create_fresh_backup()
        elif step == "refresh_plan_hashes":
            reason = (os.environ.get("L5_PLAN_HASH_REFRESH_REASON") or "").strip()
            if not reason:
                raise GateFailure(
                    failed_stage="PLAN_REFRESH",
                    failed_gate="REFRESH_REASON_REQUIRED",
                    expected="L5_PLAN_HASH_REFRESH_REASON non-empty",
                    actual=None,
                    rollback_recommendation="NO_MIGRATION_OCCURRED",
                )
            artifact["plan_hash_refresh"] = refresh_plan_hashes(reason=reason)
        elif step == "precheck":
            artifact["precheck"] = asdict(precheck())
        elif step == "l2":
            artifact["l2"] = apply_l2()
        elif step == "verify_l2":
            artifact["verify_l2"] = verify_l2()
        elif step == "l3":
            artifact["l3"] = apply_l3()
        elif step == "verify_post":
            artifact["verify_post"] = verify_post()
        elif step == "downgrade_l3":
            verify_fresh_backup(stage="DOWNGRADE_L3")
            _alembic("downgrade", REVISION_L2, stage="DOWNGRADE_L3")
        elif step == "downgrade_l2":
            verify_fresh_backup(stage="DOWNGRADE_L2")
            _alembic("downgrade", REVISION_CURRENT, stage="DOWNGRADE_L2")
        else:
            raise GateFailure(
                failed_stage="CLI",
                failed_gate="UNKNOWN_STEP",
                expected="backup|precheck|l2|verify_l2|l3|verify_post|...",
                actual=step,
                rollback_recommendation="NO_MIGRATION_OCCURRED",
            )
        ARTIFACT.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        print(json.dumps(artifact, indent=2))
        return 0
    except GateFailure as exc:
        payload = {"ok": False, **exc.as_dict()}
        print(json.dumps(payload, indent=2), file=sys.stderr)
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "L5_LAST_FAILURE.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
