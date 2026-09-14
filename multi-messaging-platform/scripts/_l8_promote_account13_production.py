#!/usr/bin/env python3
"""L8 production promotion — Account13 / Session729 ONLY (hardened).

Requires on EVERY executable path (including docker-exec worker):
  L8_ALLOW_PRODUCTION_PROMOTION=1
  L8_PRODUCTION_TARGET=account13

Never promotes 23/74/2/12/79/etc.
Never enables shadow/enforce.
Never OTP/send/worker restart.

L8_EXEC_ROLE=host|worker only selects control flow AFTER authorization.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PRODUCTION_DB = "mmp_db"
ACCOUNT_ID = 13
SESSION_ID = 729
BACKUP_MAX_AGE_MINUTES = int(os.environ.get("L8_BACKUP_MAX_AGE_MINUTES", "30"))

PG_USER = os.environ.get("PGUSER", "mmp_user")
PG_HOST = os.environ.get("PGHOST", "mmp_postgres")
REDIS = os.environ.get("L8_REDIS_CONTAINER", "mmp_redis")
CORE_API = os.environ.get("L8_CORE_API_CONTAINER", "mmp_core_api")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = PROJECT_ROOT / "reports" / "rubika-remediation" / "l8_production"
OUT = REPORT_DIR / "L8_ACCOUNT13_PROMOTION.json"


class GateFailure(RuntimeError):
    def __init__(
        self,
        *,
        failed_stage: str,
        failed_gate: str,
        expected: Any,
        actual: Any,
        rollback_performed: bool = False,
        rollback_result: Any = None,
        message: str = "",
    ) -> None:
        self.failed_stage = failed_stage
        self.failed_gate = failed_gate
        self.expected = expected
        self.actual = actual
        self.rollback_performed = rollback_performed
        self.rollback_result = rollback_result
        detail = message or f"{failed_gate}: expected={expected!r} actual={actual!r}"
        super().__init__(detail)

    def as_dict(self) -> dict[str, Any]:
        return {
            "FAILED_STAGE": self.failed_stage,
            "FAILED_GATE": self.failed_gate,
            "EXPECTED": self.expected,
            "ACTUAL": self.actual,
            "ROLLBACK_PERFORMED": self.rollback_performed,
            "ROLLBACK_RESULT": self.rollback_result,
            "PHASE_STATUS": "BLOCKED",
            "message": str(self),
        }


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _run_quiet(cmd: list[str]) -> subprocess.CompletedProcess:
    """Subprocess without echoing argv (used when command must not leak secrets)."""
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().lower()


def authorize(*, stage: str = "AUTHORIZE") -> None:
    """Required on every path that can mutate — including worker role."""
    allow = os.environ.get("L8_ALLOW_PRODUCTION_PROMOTION")
    target = os.environ.get("L8_PRODUCTION_TARGET")
    if allow != "1":
        raise GateFailure(
            failed_stage=stage,
            failed_gate="L8_ALLOW_PRODUCTION_PROMOTION",
            expected="1",
            actual=allow,
        )
    if target != "account13":
        raise GateFailure(
            failed_stage=stage,
            failed_gate="L8_PRODUCTION_TARGET",
            expected="account13",
            actual=target,
        )


def _psql(sql: str) -> str:
    r = _run(
        ["docker", "exec", PG_HOST, "psql", "-U", PG_USER, "-d", PRODUCTION_DB, "-tAc", sql]
    )
    return (r.stdout or "").strip()


def _sanitize_redis_err(text: str | None) -> str:
    """Strip URLs / password-like tokens from redis error text before reporting."""
    import re

    msg = str(text or "")
    msg = re.sub(r"redis://[^\s]+", "redis://<redacted>", msg, flags=re.I)
    msg = re.sub(r"(?i)password[=:]\S+", "password=<redacted>", msg)
    msg = re.sub(r"(?i)auth[=:]\S+", "auth=<redacted>", msg)
    return msg[:120]


def _redis_llen(key: str) -> int:
    """Authenticated LLEN via production REDIS_URL (core_api settings).

    Runs inside mmp_core_api so hostname ``redis`` resolves on the compose network.
    Never prints REDIS_URL or credentials. Fail-closed on auth/connection/malformed.
    """
    # Probe code uses get_settings().REDIS_URL in-process — not passed on argv.
    probe = (
        "import sys\n"
        "import redis\n"
        "from core_engine.config import get_settings\n"
        f"key = {key!r}\n"
        "try:\n"
        "    url = get_settings().REDIS_URL\n"
        "    if not url:\n"
        "        print('ERR:REDIS_URL_MISSING', file=sys.stderr)\n"
        "        sys.exit(11)\n"
        "    client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=5)\n"
        "    try:\n"
        "        client.ping()\n"
        "        n = client.llen(key)\n"
        "    finally:\n"
        "        client.close()\n"
        "    print(int(n))\n"
        "except Exception as exc:\n"
        "    name = type(exc).__name__\n"
        "    low = str(exc).lower()\n"
        "    if 'auth' in name.lower() or 'auth' in low or 'noauth' in low:\n"
        "        print('ERR:REDIS_AUTH_FAILED:' + name, file=sys.stderr)\n"
        "        sys.exit(12)\n"
        "    print('ERR:REDIS_CONNECTION_FAILED:' + name, file=sys.stderr)\n"
        "    sys.exit(13)\n"
    )
    print("+ docker exec mmp_core_api python -c <redis_llen_probe>", flush=True)
    r = _run_quiet(
        [
            "docker",
            "exec",
            "-e",
            "PYTHONPATH=/app",
            "-w",
            "/app",
            CORE_API,
            "python",
            "-c",
            probe,
        ]
    )
    stdout = (r.stdout or "").strip()
    stderr_safe = _sanitize_redis_err(r.stderr)
    if r.returncode == 12 or "REDIS_AUTH_FAILED" in (r.stderr or ""):
        raise GateFailure(
            failed_stage="REDIS_QUEUE",
            failed_gate="REDIS_AUTH_FAILED",
            expected="authenticated LLEN integer >= 0",
            actual=stderr_safe or "auth_failed",
        )
    if r.returncode == 11 or "REDIS_URL_MISSING" in (r.stderr or ""):
        raise GateFailure(
            failed_stage="REDIS_QUEUE",
            failed_gate="REDIS_URL_MISSING",
            expected="configured REDIS_URL",
            actual="missing",
        )
    if r.returncode != 0:
        raise GateFailure(
            failed_stage="REDIS_QUEUE",
            failed_gate="REDIS_CONNECTION_FAILED",
            expected="authenticated LLEN integer >= 0",
            actual=stderr_safe or f"exit={r.returncode}",
        )
    try:
        n = int(stdout.splitlines()[-1].strip())
    except (ValueError, IndexError):
        raise GateFailure(
            failed_stage="REDIS_QUEUE",
            failed_gate="REDIS_LLEN_MALFORMED",
            expected="integer >= 0",
            actual="unparseable",
        )
    if n < 0:
        raise GateFailure(
            failed_stage="REDIS_QUEUE",
            failed_gate="REDIS_LLEN_NEGATIVE",
            expected="integer >= 0",
            actual=n,
        )
    return n


def _api_env(name: str) -> str:
    r = _run(["docker", "exec", CORE_API, "printenv", name], check=False)
    return (r.stdout or "").strip()


def fresh_backup() -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump = REPORT_DIR / f"mmp_db_pre_account13_{ts}.dump"
    _run(
        [
            "docker",
            "exec",
            PG_HOST,
            "pg_dump",
            "-U",
            PG_USER,
            "-Fc",
            "-f",
            f"/tmp/{dump.name}",
            PRODUCTION_DB,
        ]
    )
    _run(["docker", "cp", f"{PG_HOST}:/tmp/{dump.name}", str(dump)])
    digest = _sha256_file(dump)
    size = dump.stat().st_size
    meta = {
        "BACKUP_PATH": str(dump.resolve()),
        "backup_path": str(dump.resolve()),
        "BACKUP_SHA256": digest,
        "sha256": digest,
        "BACKUP_TIMESTAMP": ts,
        "created_at": ts,
        "BACKUP_SIZE": size,
        "size_bytes": size,
        "source_db": PRODUCTION_DB,
    }
    meta_path = REPORT_DIR / f"{dump.name}.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    meta["meta_path"] = str(meta_path.resolve())
    return meta


def verify_backup_before_mutation(backup: dict[str, Any]) -> dict[str, Any]:
    """Hard gates: exists, nonempty, SHA re-verify, provenance, freshness."""
    path = Path(str(backup.get("BACKUP_PATH") or backup.get("backup_path") or ""))
    if not path.is_file():
        raise GateFailure(
            failed_stage="BACKUP_VERIFY",
            failed_gate="BACKUP_EXISTS",
            expected=str(path),
            actual="missing",
        )
    size = path.stat().st_size
    if size <= 0:
        raise GateFailure(
            failed_stage="BACKUP_VERIFY",
            failed_gate="BACKUP_NONEMPTY",
            expected=">0",
            actual=size,
        )
    recorded_sha = str(backup.get("BACKUP_SHA256") or backup.get("sha256") or "").lower()
    recomputed = _sha256_file(path)
    if not recorded_sha or recomputed != recorded_sha:
        raise GateFailure(
            failed_stage="BACKUP_VERIFY",
            failed_gate="BACKUP_SHA_REVERIFY",
            expected=recorded_sha,
            actual=recomputed,
        )
    recorded_size = int(backup.get("BACKUP_SIZE") or backup.get("size_bytes") or -1)
    if recorded_size != size:
        raise GateFailure(
            failed_stage="BACKUP_VERIFY",
            failed_gate="BACKUP_SIZE_MATCH",
            expected=recorded_size,
            actual=size,
        )
    meta_path = Path(str(backup.get("meta_path") or (str(path) + ".meta.json")))
    if not meta_path.is_file():
        raise GateFailure(
            failed_stage="BACKUP_VERIFY",
            failed_gate="BACKUP_META_EXISTS",
            expected=str(meta_path),
            actual="missing",
        )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("source_db") != PRODUCTION_DB:
        raise GateFailure(
            failed_stage="BACKUP_VERIFY",
            failed_gate="BACKUP_SOURCE_DB",
            expected=PRODUCTION_DB,
            actual=meta.get("source_db"),
        )
    meta_path_val = str(Path(str(meta.get("BACKUP_PATH") or meta.get("backup_path") or "")).resolve())
    if meta_path_val != str(path.resolve()):
        raise GateFailure(
            failed_stage="BACKUP_VERIFY",
            failed_gate="BACKUP_PATH_META",
            expected=str(path.resolve()),
            actual=meta_path_val,
        )
    if int(meta.get("BACKUP_SIZE") or meta.get("size_bytes") or -1) != size:
        raise GateFailure(
            failed_stage="BACKUP_VERIFY",
            failed_gate="BACKUP_SIZE_META",
            expected=size,
            actual=meta.get("BACKUP_SIZE") or meta.get("size_bytes"),
        )
    meta_sha = str(meta.get("BACKUP_SHA256") or meta.get("sha256") or "").lower()
    if meta_sha != recomputed:
        raise GateFailure(
            failed_stage="BACKUP_VERIFY",
            failed_gate="BACKUP_SHA_META",
            expected=recomputed,
            actual=meta_sha,
        )
    created_raw = str(meta.get("created_at") or meta.get("BACKUP_TIMESTAMP") or "")
    try:
        created = datetime.strptime(created_raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise GateFailure(
            failed_stage="BACKUP_VERIFY",
            failed_gate="BACKUP_CREATED_AT_PARSE",
            expected="%Y%m%dT%H%M%SZ",
            actual=created_raw,
            message=str(exc),
        ) from exc
    age = datetime.now(timezone.utc) - created
    if age > timedelta(minutes=BACKUP_MAX_AGE_MINUTES) or age < timedelta(0):
        raise GateFailure(
            failed_stage="BACKUP_VERIFY",
            failed_gate="BACKUP_FRESHNESS",
            expected=f"age<={BACKUP_MAX_AGE_MINUTES}m",
            actual=str(age),
        )
    return {
        "verified": True,
        "path": str(path.resolve()),
        "sha256": recomputed,
        "size_bytes": size,
        "age_seconds": int(age.total_seconds()),
        "source_db": PRODUCTION_DB,
    }


def snapshot_inventory() -> dict[str, Any]:
    sessions = _psql(
        "SELECT id||':'||account_id||':'||session_status::text "
        "FROM channel_sessions ORDER BY id;"
    )
    challenges = _psql(
        "SELECT COUNT(*) FROM rubika_login_challenges;"
    )
    attempts = _psql("SELECT COUNT(*) FROM message_attempts;")
    return {
        "total_sessions": int(_psql("SELECT COUNT(*) FROM channel_sessions;") or "0"),
        "session_inventory": sessions,
        "a23": _psql(
            "SELECT id||':'||session_status::text FROM channel_sessions "
            "WHERE account_id=23 ORDER BY id;"
        ),
        "a74": _psql(
            "SELECT id||':'||session_status::text FROM channel_sessions "
            "WHERE account_id=74 ORDER BY id;"
        ),
        "a12": _psql(
            "SELECT id||':'||session_status::text FROM channel_sessions "
            "WHERE account_id=12 ORDER BY id;"
        ),
        "a79": _psql(
            "SELECT id||':'||session_status::text FROM channel_sessions "
            "WHERE account_id=79 ORDER BY id;"
        ),
        "active_global": int(
            _psql(
                "SELECT COUNT(*) FROM channel_sessions WHERE session_status::text='active';"
            )
            or "0"
        ),
        "login_challenge_count": int(challenges or "0"),
        "message_attempt_count": int(attempts or "0"),
        "queue13": _redis_llen("queue:rubika:13"),
    }


def host_precheck() -> dict[str, Any]:
    mode = (_api_env("RUBIKA_CANONICAL_SESSION_MODE") or "off").strip().lower()
    if mode in {"", "false"}:
        mode = "off"
    if mode != "off":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="RUBIKA_CANONICAL_SESSION_MODE",
            expected="off",
            actual=mode,
        )
    v1 = (_api_env("RUBIKA_CANONICAL_SESSION_V1") or "false").strip().lower()
    if v1 in {"1", "true", "yes", "on"}:
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="RUBIKA_CANONICAL_SESSION_V1",
            expected="false",
            actual=v1,
        )
    q = _redis_llen("queue:rubika:13")
    if q != 0:
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="QUEUE13",
            expected=0,
            actual=q,
        )
    exists = _psql("SELECT COUNT(*) FROM accounts WHERE id=13;")
    if exists != "1":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="ACCOUNT13_EXISTS",
            expected="1",
            actual=exists,
        )
    mapping = _psql(
        "SELECT account_id||':'||session_status::text FROM channel_sessions WHERE id=729;"
    )
    if mapping != "13:legacy_unclassified":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="SESSION729_MAPPING_STATUS",
            expected="13:legacy_unclassified",
            actual=mapping,
        )
    active13 = _psql(
        "SELECT COUNT(*) FROM channel_sessions WHERE account_id=13 "
        "AND session_status::text='active';"
    )
    if active13 != "0":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="ACCOUNT13_ACTIVE_ZERO",
            expected="0",
            actual=active13,
        )
    running = _psql(
        "SELECT COUNT(*) FROM campaigns c "
        "JOIN campaign_accounts ca ON ca.campaign_id=c.id "
        "WHERE ca.account_id=13 AND c.status::text='running';"
    )
    if running != "0":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="NO_RUNNING_CAMPAIGN",
            expected="0",
            actual=running,
        )
    return {"mode": mode, "queue13": q, "mapping": mapping}


def _assert_eq(stage: str, gate: str, expected: Any, actual: Any) -> None:
    if expected != actual:
        raise GateFailure(
            failed_stage=stage,
            failed_gate=gate,
            expected=expected,
            actual=actual,
        )


async def promote_and_verify() -> dict[str, Any]:
    """Worker role: authorize again, then mutate Account13/729 only."""
    authorize(stage="WORKER_AUTHORIZE")

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from core_engine.services.rubika_candidate_prover import RealRubikaCandidateProver
    from core_engine.services.rubika_canonical_runtime import select_legacy_rubika_session_row
    from core_engine.services.rubika_canonical_session import load_canonical_rubika_session
    from core_engine.services.rubika_legacy_promotion import (
        LegacyPromotionEvidence,
        promote_proven_legacy_rubika_session,
        rollback_legacy_promotion,
        verify_post_promotion,
    )

    url = os.environ.get("DATABASE_URL") or ""
    name = (urlparse(url).path or "").lstrip("/").split("?")[0]
    if name != PRODUCTION_DB:
        raise GateFailure(
            failed_stage="WORKER_DB",
            failed_gate="DATABASE_URL_MMP_DB",
            expected=PRODUCTION_DB,
            actual=name,
        )

    engine = create_engine(url)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    artifact: dict[str, Any] = {
        "otp_requested": False,
        "message_sent": False,
        "rolled_back": False,
        "ROLLBACK_PERFORMED": False,
        "ROLLBACK_RESULT": None,
    }
    try:
        before_attempts = int(
            db.execute(text("SELECT COUNT(*) FROM message_attempts")).scalar() or 0
        )
        before_challenges = int(
            db.execute(text("SELECT COUNT(*) FROM rubika_login_challenges")).scalar() or 0
        )

        def queue_len(_aid: int) -> int:
            return int(os.environ.get("L8_QUEUE13_LEN", "0"))

        promo = await promote_proven_legacy_rubika_session(
            db,
            account_id=ACCOUNT_ID,
            session_id=SESSION_ID,
            expected_identity=None,
            evidence=LegacyPromotionEvidence(
                source_phase="L8_PRODUCTION_ACCOUNT13",
                operator_note="operator-approved Account13 only",
            ),
            prover=RealRubikaCandidateProver(),
            queue_length_fn=queue_len,
        )
        artifact["promotion"] = promo.as_safe_dict()
        if not promo.ok:
            raise GateFailure(
                failed_stage="PROMOTE",
                failed_gate="PROMOTE_PROVEN_LEGACY",
                expected="PROMOTION_OK",
                actual=promo.code,
            )

        ver = verify_post_promotion(db, account_id=ACCOUNT_ID, session_id=SESSION_ID)
        artifact["verify_post_promotion"] = ver
        proof = await RealRubikaCandidateProver().prove_detailed(
            db,
            account_id=ACCOUNT_ID,
            session_id=SESSION_ID,
            expected_guid=promo.proven_identity_guid,
        )
        artifact["canonical_reconnect"] = {
            "ok": proof.ok,
            "code": proof.error_code or proof.proof_status,
            "identity_match": proof.identity_match,
            "duration_ms": proof.duration_ms,
            "sanitized_message": proof.sanitized_message,
        }
        loaded = load_canonical_rubika_session(db, ACCOUNT_ID)
        legacy = select_legacy_rubika_session_row(db, ACCOUNT_ID)
        artifact["canonical_loader_session_id"] = int(loaded.session_id)
        artifact["legacy_loader_session_id"] = int(legacy.id) if legacy else None
        artifact["LEGACY_CANONICAL_MATCH"] = (
            int(loaded.session_id) == SESSION_ID
            and legacy is not None
            and int(legacy.id) == SESSION_ID
        )
        after_attempts = int(
            db.execute(text("SELECT COUNT(*) FROM message_attempts")).scalar() or 0
        )
        after_challenges = int(
            db.execute(text("SELECT COUNT(*) FROM rubika_login_challenges")).scalar() or 0
        )
        artifact["message_attempts_before"] = before_attempts
        artifact["message_attempts_after"] = after_attempts
        artifact["login_challenges_before"] = before_challenges
        artifact["login_challenges_after"] = after_challenges

        inner_ok = True
        fail_gate = None
        fail_expected: Any = None
        fail_actual: Any = None
        checks = [
            ("ACTIVE_COUNT", 1, ver.get("active_count")),
            ("ACTIVE_SESSION_ID", SESSION_ID, ver.get("canonical_session_id")),
            ("CANONICAL_OK", True, ver.get("canonical_ok")),
            ("LEGACY_CANONICAL_MATCH", True, artifact["LEGACY_CANONICAL_MATCH"]),
            ("CANONICAL_RECONNECT", True, proof.ok),
            ("IDENTITY_MATCH", True, proof.identity_match is True),
            ("MESSAGE_ATTEMPTS_UNCHANGED", before_attempts, after_attempts),
            ("LOGIN_CHALLENGES_UNCHANGED", before_challenges, after_challenges),
            ("CANONICAL_LOADER_ID", SESSION_ID, artifact["canonical_loader_session_id"]),
            ("LEGACY_LOADER_ID", SESSION_ID, artifact["legacy_loader_session_id"]),
        ]
        for gate, expected, actual in checks:
            if expected != actual:
                inner_ok = False
                fail_gate = gate
                fail_expected = expected
                fail_actual = actual
                break

        artifact["ACCOUNT13_PROMOTION_PASS"] = bool(inner_ok)
        artifact["ACCOUNT13_ACTIVE_SESSION_ID"] = SESSION_ID if inner_ok else None
        artifact["ACCOUNT13_CANONICAL_LOADER_PASS"] = bool(
            ver.get("canonical_ok") and ver.get("canonical_session_id") == SESSION_ID
        )
        artifact["ACCOUNT13_LEGACY_CANONICAL_MATCH"] = bool(
            artifact["LEGACY_CANONICAL_MATCH"]
        )

        if not inner_ok:
            rb = rollback_legacy_promotion(
                db,
                account_id=ACCOUNT_ID,
                session_id=SESSION_ID,
                identity_snapshot_before=promo.identity_snapshot_before,
            )
            artifact["ROLLBACK_PERFORMED"] = True
            artifact["rolled_back"] = True
            artifact["ROLLBACK_RESULT"] = {
                "ok": rb.ok,
                "code": rb.code,
                "identity_restored": rb.identity_restored,
            }
            raise GateFailure(
                failed_stage="POST_VERIFY_INNER",
                failed_gate=fail_gate or "POST_VERIFY",
                expected=fail_expected,
                actual=fail_actual,
                rollback_performed=True,
                rollback_result=artifact["ROLLBACK_RESULT"],
            )
        return artifact
    finally:
        db.close()
        engine.dispose()


def _inventory_excluding_target(inv: str) -> str:
    """Drop session 729 from inventory for unrelated-status comparison."""
    parts = []
    for tok in (inv or "").split():
        if not tok.startswith(f"{SESSION_ID}:"):
            parts.append(tok)
    return " ".join(parts)


def assert_host_postconditions(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    inner: dict[str, Any],
) -> None:
    stage = "POST_VERIFY_HOST"
    _assert_eq(stage, "ACCOUNT13_PROMOTION_PASS", True, inner.get("ACCOUNT13_PROMOTION_PASS"))
    _assert_eq(stage, "ACCOUNT13_ACTIVE_SESSION_ID", SESSION_ID, inner.get("ACCOUNT13_ACTIVE_SESSION_ID"))
    _assert_eq(stage, "ACCOUNT13_CANONICAL_LOADER_PASS", True, inner.get("ACCOUNT13_CANONICAL_LOADER_PASS"))
    _assert_eq(
        stage,
        "ACCOUNT13_LEGACY_CANONICAL_MATCH",
        True,
        inner.get("ACCOUNT13_LEGACY_CANONICAL_MATCH"),
    )
    _assert_eq(stage, "GLOBAL_ACTIVE_SESSION_COUNT", 1, after["active_global"])
    _assert_eq(stage, "QUEUE13", 0, after["queue13"])
    _assert_eq(
        stage,
        "TOTAL_CHANNEL_SESSION_COUNT",
        before["total_sessions"],
        after["total_sessions"],
    )
    _assert_eq(stage, "ACCOUNT23_UNCHANGED", before["a23"], after["a23"])
    _assert_eq(stage, "ACCOUNT74_UNCHANGED", before["a74"], after["a74"])
    _assert_eq(stage, "ACCOUNT12_UNCHANGED", before["a12"], after["a12"])
    _assert_eq(stage, "ACCOUNT79_UNCHANGED", before["a79"], after["a79"])
    _assert_eq(
        stage,
        "MESSAGE_ATTEMPT_COUNT",
        before["message_attempt_count"],
        after["message_attempt_count"],
    )
    _assert_eq(
        stage,
        "LOGIN_CHALLENGE_COUNT",
        before["login_challenge_count"],
        after["login_challenge_count"],
    )
    before_other = _inventory_excluding_target(before["session_inventory"])
    after_other = _inventory_excluding_target(after["session_inventory"])
    _assert_eq(stage, "UNRELATED_SESSION_STATUS_INVENTORY", before_other, after_other)
    # Target must be ACTIVE after success
    if f"{SESSION_ID}:13:active" not in (after["session_inventory"] or ""):
        raise GateFailure(
            failed_stage=stage,
            failed_gate="SESSION729_ACTIVE_IN_INVENTORY",
            expected=f"{SESSION_ID}:13:active",
            actual=after["session_inventory"],
        )


def run_host() -> dict[str, Any]:
    authorize(stage="HOST_AUTHORIZE")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    before = snapshot_inventory()
    backup = fresh_backup()
    backup_verify = verify_backup_before_mutation(backup)
    pre = host_precheck()
    # Re-verify backup immediately before docker mutation
    backup_verify_2 = verify_backup_before_mutation(backup)

    q13 = pre["queue13"]
    env_args = [
        "-e",
        "L8_EXEC_ROLE=worker",
        "-e",
        "L8_ALLOW_PRODUCTION_PROMOTION=1",
        "-e",
        "L8_PRODUCTION_TARGET=account13",
        "-e",
        f"L8_QUEUE13_LEN={q13}",
        "-e",
        "PYTHONPATH=/app",
        "-e",
        "RUBIKA_CANONICAL_SESSION_MODE=off",
    ]
    cmd = [
        "docker",
        "exec",
        *env_args,
        "-w",
        "/app",
        CORE_API,
        "python",
        "scripts/_l8_promote_account13_production.py",
    ]
    started = _run(cmd, check=False)
    out = (started.stdout or "").strip()
    err = (started.stderr or "").strip()
    inner: dict[str, Any] | None = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{") and (
            "ACCOUNT13_PROMOTION_PASS" in line or "FAILED_STAGE" in line
        ):
            inner = json.loads(line)
            break
    if inner is None:
        try:
            inner = json.loads(out)
        except Exception as exc:
            raise GateFailure(
                failed_stage="WORKER_OUTPUT",
                failed_gate="PARSE_INNER_JSON",
                expected="json artifact",
                actual=(out[-500:] + "\n" + err[-500:]),
                message=str(exc),
            ) from exc

    if inner.get("FAILED_STAGE"):
        raise GateFailure(
            failed_stage=str(inner.get("FAILED_STAGE")),
            failed_gate=str(inner.get("FAILED_GATE")),
            expected=inner.get("EXPECTED"),
            actual=inner.get("ACTUAL"),
            rollback_performed=bool(inner.get("ROLLBACK_PERFORMED")),
            rollback_result=inner.get("ROLLBACK_RESULT"),
        )

    after = snapshot_inventory()
    assert_host_postconditions(before=before, after=after, inner=inner)

    artifact = {
        "phase": "L8_BATCH_A_ACCOUNT13_PROMOTION",
        "PHASE_STATUS": "COMPLETE",
        "backup": backup,
        "backup_verify": backup_verify,
        "backup_verify_immediate_pre_mutation": backup_verify_2,
        "precheck": pre,
        "before": before,
        "after": after,
        "inner": inner,
        "ACCOUNT13_PROMOTION_PASS": True,
        "ACCOUNT13_ACTIVE_SESSION_ID": SESSION_ID,
        "ACCOUNT13_CANONICAL_LOADER_PASS": True,
        "ACCOUNT13_LEGACY_CANONICAL_MATCH": True,
        "GLOBAL_ACTIVE_SESSION_COUNT": after["active_global"],
        "ACCOUNT23_UNCHANGED": True,
        "ACCOUNT74_UNCHANGED": True,
        "ACCOUNT12_UNCHANGED": True,
        "ACCOUNT79_UNCHANGED": True,
        "TOTAL_CHANNEL_SESSION_COUNT_UNCHANGED": True,
        "MESSAGE_SENT": False,
        "OTP_REQUESTED": False,
        "queue13_after": after["queue13"],
        "EXACT_OPERATOR_INPUT_REQUIRED": (
            "Review Account13 before approving Account23 promotion"
        ),
    }
    return artifact


def main() -> int:
    # Authorization is mandatory for ALL roles — no L8_INNER bypass.
    authorize(stage="ENTRY_AUTHORIZE")
    role = (os.environ.get("L8_EXEC_ROLE") or "host").strip().lower()
    try:
        if role == "worker":
            # Mutating path inside core_api — authorize already proven above.
            payload = asyncio.run(promote_and_verify())
            print(json.dumps(payload))
            return 0
        if role != "host":
            raise GateFailure(
                failed_stage="ENTRY",
                failed_gate="L8_EXEC_ROLE",
                expected="host|worker",
                actual=role,
            )
        artifact = run_host()
        OUT.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        print(json.dumps(artifact, indent=2))
        return 0
    except GateFailure as exc:
        payload = {"ok": False, **exc.as_dict()}
        print(json.dumps(payload, indent=2), file=sys.stderr)
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "L8_ACCOUNT13_LAST_FAILURE.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        # Never emit COMPLETE on failure.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
