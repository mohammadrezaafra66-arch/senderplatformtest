#!/usr/bin/env python3
"""L8 production promotion — Account74 / Session724 ONLY (hardened).

Requires on EVERY executable path (including docker-exec worker):
  L8_ALLOW_PRODUCTION_PROMOTION=1
  L8_PRODUCTION_TARGET=account74

Never promotes 2/12/79/etc. Do not modify Account13 or Account23.
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
ACCOUNT_ID = 74
SESSION_ID = 724
BACKUP_MAX_AGE_MINUTES = int(os.environ.get("L8_BACKUP_MAX_AGE_MINUTES", "30"))

PG_USER = os.environ.get("PGUSER", "mmp_user")
PG_HOST = os.environ.get("PGHOST", "mmp_postgres")
REDIS = os.environ.get("L8_REDIS_CONTAINER", "mmp_redis")
CORE_API = os.environ.get("L8_CORE_API_CONTAINER", "mmp_core_api")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = PROJECT_ROOT / "reports" / "rubika-remediation" / "l8_production"
OUT = REPORT_DIR / "L8_ACCOUNT74_PROMOTION.json"


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
    if target != "account74":
        raise GateFailure(
            failed_stage=stage,
            failed_gate="L8_PRODUCTION_TARGET",
            expected="account74",
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
    dump = REPORT_DIR / f"mmp_db_pre_account74_{ts}.dump"
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
        "a13": _psql(
            "SELECT id||':'||session_status::text FROM channel_sessions "
            "WHERE account_id=13 ORDER BY id;"
        ),
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
        "queue74": _redis_llen("queue:rubika:74"),
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
    q = _redis_llen("queue:rubika:74")
    if q != 0:
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="QUEUE74",
            expected=0,
            actual=q,
        )
    exists = _psql("SELECT COUNT(*) FROM accounts WHERE id=74;")
    if exists != "1":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="ACCOUNT74_EXISTS",
            expected="1",
            actual=exists,
        )
    mapping = _psql(
        "SELECT account_id||':'||session_status::text FROM channel_sessions WHERE id=724;"
    )
    if mapping != "74:legacy_unclassified":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="SESSION724_MAPPING_STATUS",
            expected="74:legacy_unclassified",
            actual=mapping,
        )
    active74 = _psql(
        "SELECT COUNT(*) FROM channel_sessions WHERE account_id=74 "
        "AND session_status::text='active';"
    )
    if active74 != "0":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="ACCOUNT74_ACTIVE_ZERO",
            expected="0",
            actual=active74,
        )
    # Account13 ACTIVE 729 + Account23 ACTIVE 725; global ACTIVE == 2
    a13 = _psql(
        "SELECT id||':'||session_status::text FROM channel_sessions "
        "WHERE account_id=13 ORDER BY id;"
    )
    if a13 != "729:active":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="ACCOUNT13_REMAINS_ACTIVE_729",
            expected="729:active",
            actual=a13,
        )
    a23 = _psql(
        "SELECT id||':'||session_status::text FROM channel_sessions "
        "WHERE account_id=23 ORDER BY id;"
    )
    if a23 != "725:active":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="ACCOUNT23_REMAINS_ACTIVE_725",
            expected="725:active",
            actual=a23,
        )
    active_global = _psql(
        "SELECT COUNT(*) FROM channel_sessions WHERE session_status::text='active';"
    )
    if active_global != "2":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="GLOBAL_ACTIVE_SESSION_COUNT",
            expected="2",
            actual=active_global,
        )
    running = _psql(
        "SELECT COUNT(*) FROM campaigns c "
        "JOIN campaign_accounts ca ON ca.campaign_id=c.id "
        "WHERE ca.account_id=74 AND c.status::text='running';"
    )
    if running != "0":
        raise GateFailure(
            failed_stage="PRECHECK",
            failed_gate="NO_RUNNING_CAMPAIGN",
            expected="0",
            actual=running,
        )
    return {
        "mode": mode,
        "queue74": q,
        "mapping": mapping,
        "a13": a13,
        "a23": a23,
        "active_global": int(active_global),
    }


def _assert_eq(stage: str, gate: str, expected: Any, actual: Any) -> None:
    if expected != actual:
        raise GateFailure(
            failed_stage=stage,
            failed_gate=gate,
            expected=expected,
            actual=actual,
        )


def _identity_snapshot_to_dict(snapshot: Any) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    status = getattr(snapshot, "rubika_identity_status", None)
    verified_at = getattr(snapshot, "rubika_identity_verified_at", None)
    return {
        "rubika_guid": getattr(snapshot, "rubika_guid", None),
        "rubika_identity_status": (
            status.value if hasattr(status, "value") else (str(status) if status else None)
        ),
        "rubika_identity_verified_at": (
            verified_at.isoformat() if verified_at is not None else None
        ),
        "newly_bound": bool(getattr(snapshot, "newly_bound", False)),
    }


def _identity_snapshot_from_dict(data: dict[str, Any] | None) -> Any:
    from core_engine.models import RubikaIdentityStatus
    from core_engine.services.rubika_legacy_promotion import IdentitySnapshot

    if not data:
        return IdentitySnapshot(
            rubika_guid=None,
            rubika_identity_status=None,
            rubika_identity_verified_at=None,
            newly_bound=False,
        )
    status_raw = data.get("rubika_identity_status")
    status = None
    if status_raw:
        try:
            status = RubikaIdentityStatus(str(status_raw))
        except ValueError:
            status = None
    verified_raw = data.get("rubika_identity_verified_at")
    verified_at = None
    if verified_raw:
        try:
            verified_at = datetime.fromisoformat(str(verified_raw))
        except ValueError:
            verified_at = None
    return IdentitySnapshot(
        rubika_guid=(str(data["rubika_guid"]).strip() or None)
        if data.get("rubika_guid") is not None
        else None,
        rubika_identity_status=status,
        rubika_identity_verified_at=verified_at,
        newly_bound=bool(data.get("newly_bound")),
    )


def promotion_committed(inner: dict[str, Any] | None) -> bool:
    """True only when Account74/724 promotion reached a committed success path."""
    if not inner:
        return False
    if inner.get("FAILED_STAGE"):
        return False
    if inner.get("promotion_committed") is True:
        return True
    return bool(inner.get("ACCOUNT74_PROMOTION_PASS"))


def verify_account74_restored_to_prepromotion(
    before: dict[str, Any],
    *,
    identity_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Host-side check that Session724 / Account74 match pre-promotion invariants."""
    a74 = _psql(
        "SELECT id||':'||session_status::text FROM channel_sessions "
        "WHERE account_id=74 ORDER BY id;"
    )
    mapping = _psql(
        "SELECT account_id||':'||session_status::text FROM channel_sessions WHERE id=724;"
    )
    guid = _psql("SELECT COALESCE(rubika_guid, '') FROM accounts WHERE id=74;")
    status = _psql(
        "SELECT COALESCE(rubika_identity_status::text, '') FROM accounts WHERE id=74;"
    )
    ok = True
    detail: dict[str, Any] = {
        "a74": a74,
        "mapping": mapping,
        "rubika_guid": guid,
        "rubika_identity_status": status,
    }
    if a74 != before.get("a74"):
        ok = False
        detail["a74_mismatch"] = {"expected": before.get("a74"), "actual": a74}
    if mapping != "74:legacy_unclassified":
        ok = False
        detail["mapping_mismatch"] = {
            "expected": "74:legacy_unclassified",
            "actual": mapping,
        }
    snap = identity_snapshot or {}
    if snap.get("newly_bound"):
        expected_guid = str(snap.get("rubika_guid") or "").strip()
        if guid != expected_guid:
            ok = False
            detail["guid_mismatch"] = {"expected": expected_guid, "actual": guid}
        expected_status = str(snap.get("rubika_identity_status") or "unbound").strip().lower()
        if expected_status and status.lower() not in {expected_status, ""}:
            # unbound may serialize differently; accept empty/unbound when snapshot unbound
            if not (
                expected_status in {"unbound", ""}
                and status.lower() in {"unbound", ""}
            ):
                ok = False
                detail["status_mismatch"] = {
                    "expected": expected_status,
                    "actual": status,
                }
    detail["ok"] = ok
    return detail


def invoke_exact_rollback_via_worker(
    identity_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Call reviewed rollback_legacy_promotion for Account74/724 only (core_api)."""
    snap_json = json.dumps(identity_snapshot or {})
    env_args = [
        "-e",
        "L8_EXEC_ROLE=rollback",
        "-e",
        "L8_ALLOW_PRODUCTION_PROMOTION=1",
        "-e",
        "L8_PRODUCTION_TARGET=account74",
        "-e",
        f"L8_IDENTITY_SNAPSHOT_JSON={snap_json}",
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
        "scripts/_l8_promote_account74_production.py",
    ]
    started = _run(cmd, check=False)
    out = (started.stdout or "").strip()
    err = (started.stderr or "").strip()
    payload: dict[str, Any] | None = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{") and (
            "ROLLBACK_RESULT" in line or "FAILED_STAGE" in line or '"ok"' in line
        ):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if payload is None:
        try:
            payload = json.loads(out)
        except Exception:
            return {
                "ok": False,
                "ROLLBACK_RESULT": "FAIL",
                "code": "ROLLBACK_WORKER_PARSE",
                "stderr_tail": err[-300:],
                "stdout_tail": out[-300:],
            }
    ok = bool(payload.get("ok")) and str(payload.get("ROLLBACK_RESULT", "")).upper() == "PASS"
    if payload.get("ROLLBACK_RESULT") is None and payload.get("ok") is True:
        ok = True
    return {
        "ok": ok,
        "ROLLBACK_RESULT": "PASS" if ok else "FAIL",
        "detail": payload,
    }


def run_rollback_worker() -> dict[str, Any]:
    """Worker role: exact-ID rollback for Account74 / Session724 only."""
    authorize(stage="ROLLBACK_AUTHORIZE")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from core_engine.services.rubika_legacy_promotion import rollback_legacy_promotion

    url = os.environ.get("DATABASE_URL") or ""
    name = (urlparse(url).path or "").lstrip("/").split("?")[0]
    if name != PRODUCTION_DB:
        raise GateFailure(
            failed_stage="ROLLBACK_DB",
            failed_gate="DATABASE_URL_MMP_DB",
            expected=PRODUCTION_DB,
            actual=name,
        )
    raw = os.environ.get("L8_IDENTITY_SNAPSHOT_JSON") or "{}"
    try:
        snap_dict = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GateFailure(
            failed_stage="ROLLBACK",
            failed_gate="IDENTITY_SNAPSHOT_JSON",
            expected="json object",
            actual="unparseable",
            message=str(exc),
        ) from exc
    snapshot = _identity_snapshot_from_dict(snap_dict if isinstance(snap_dict, dict) else None)

    engine = create_engine(url)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        rb = rollback_legacy_promotion(
            db,
            account_id=ACCOUNT_ID,
            session_id=SESSION_ID,
            identity_snapshot_before=snapshot,
        )
        return {
            "ok": bool(rb.ok),
            "ROLLBACK_RESULT": "PASS" if rb.ok else "FAIL",
            "code": rb.code,
            "account_id": ACCOUNT_ID,
            "session_id": SESSION_ID,
            "identity_restored": rb.identity_restored,
        }
    finally:
        db.close()
        engine.dispose()


def handle_host_postcheck_failure_after_commit(
    *,
    before: dict[str, Any],
    post_failure: GateFailure,
    inner: dict[str, Any],
    rollback_invoker: Any = None,
    restore_verifier: Any = None,
) -> GateFailure:
    """If promotion committed and host postcheck fails, invoke exact 74/724 rollback.

    Never returns a COMPLETE artifact — always a GateFailure.
    """
    invoker = rollback_invoker or invoke_exact_rollback_via_worker
    verifier = restore_verifier or verify_account74_restored_to_prepromotion
    snap = inner.get("identity_snapshot_before")
    rb = invoker(snap if isinstance(snap, dict) else None)
    rb_ok = bool(rb.get("ok")) and str(rb.get("ROLLBACK_RESULT", "FAIL")).upper() == "PASS"
    restore: dict[str, Any] | None = None
    if rb_ok:
        restore = verifier(
            before,
            identity_snapshot=snap if isinstance(snap, dict) else None,
        )
        if not restore.get("ok"):
            rb_ok = False
    msg = ""
    if not rb_ok:
        msg = (
            "MANUAL_INTERVENTION_REQUIRED: host postcheck failed after Account74 "
            "promotion commit and exact rollback did not restore pre-promotion state. "
            "Do not auto full-DB restore; intervene for Account74/Session724 only."
        )
    return GateFailure(
        failed_stage="POST_VERIFY",
        failed_gate=post_failure.failed_gate,
        expected=post_failure.expected,
        actual=post_failure.actual,
        rollback_performed=True,
        rollback_result="PASS" if rb_ok else "FAIL",
        message=msg
        or (
            f"host postcheck failed; exact rollback ROLLBACK_RESULT="
            f"{'PASS' if rb_ok else 'FAIL'} restore={restore}"
        ),
    )


async def promote_and_verify() -> dict[str, Any]:
    """Worker role: authorize again, then mutate Account74/724 only."""
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
            return int(os.environ.get("L8_QUEUE74_LEN", "0"))

        promo = await promote_proven_legacy_rubika_session(
            db,
            account_id=ACCOUNT_ID,
            session_id=SESSION_ID,
            expected_identity=None,
            evidence=LegacyPromotionEvidence(
                source_phase="L8_PRODUCTION_ACCOUNT74",
                operator_note="operator-approved Account74 only",
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

        artifact["ACCOUNT74_PROMOTION_PASS"] = bool(inner_ok)
        artifact["ACCOUNT74_ACTIVE_SESSION_ID"] = SESSION_ID if inner_ok else None
        artifact["ACCOUNT74_CANONICAL_LOADER_PASS"] = bool(
            ver.get("canonical_ok") and ver.get("canonical_session_id") == SESSION_ID
        )
        artifact["ACCOUNT74_LEGACY_CANONICAL_MATCH"] = bool(
            artifact["LEGACY_CANONICAL_MATCH"]
        )
        # Needed by host-path auto-rollback if a later host postcheck fails.
        artifact["identity_snapshot_before"] = _identity_snapshot_to_dict(
            promo.identity_snapshot_before
        )
        artifact["promotion_committed"] = bool(inner_ok)

        if not inner_ok:
            rb = rollback_legacy_promotion(
                db,
                account_id=ACCOUNT_ID,
                session_id=SESSION_ID,
                identity_snapshot_before=promo.identity_snapshot_before,
            )
            artifact["ROLLBACK_PERFORMED"] = True
            artifact["rolled_back"] = True
            artifact["ROLLBACK_RESULT"] = "PASS" if rb.ok else "FAIL"
            artifact["ROLLBACK_DETAIL"] = {
                "ok": rb.ok,
                "code": rb.code,
                "identity_restored": rb.identity_restored,
            }
            raise GateFailure(
                failed_stage="POST_VERIFY",
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
    """Drop session 724 from inventory for unrelated-status comparison."""
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
    _assert_eq(stage, "ACCOUNT74_PROMOTION_PASS", True, inner.get("ACCOUNT74_PROMOTION_PASS"))
    _assert_eq(stage, "ACCOUNT74_ACTIVE_SESSION_ID", SESSION_ID, inner.get("ACCOUNT74_ACTIVE_SESSION_ID"))
    _assert_eq(stage, "ACCOUNT74_CANONICAL_LOADER_PASS", True, inner.get("ACCOUNT74_CANONICAL_LOADER_PASS"))
    _assert_eq(
        stage,
        "ACCOUNT74_LEGACY_CANONICAL_MATCH",
        True,
        inner.get("ACCOUNT74_LEGACY_CANONICAL_MATCH"),
    )
    _assert_eq(stage, "GLOBAL_ACTIVE_SESSION_COUNT", 3, after["active_global"])
    _assert_eq(stage, "QUEUE74", 0, after["queue74"])
    _assert_eq(
        stage,
        "TOTAL_CHANNEL_SESSION_COUNT",
        before["total_sessions"],
        after["total_sessions"],
    )
    _assert_eq(stage, "ACCOUNT13_UNCHANGED", before["a13"], after["a13"])
    _assert_eq(stage, "ACCOUNT13_STILL_ACTIVE_729", "729:active", after["a13"])
    _assert_eq(stage, "ACCOUNT23_UNCHANGED", before["a23"], after["a23"])
    _assert_eq(stage, "ACCOUNT23_STILL_ACTIVE_725", "725:active", after["a23"])
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
    if f"{SESSION_ID}:74:active" not in (after["session_inventory"] or ""):
        raise GateFailure(
            failed_stage=stage,
            failed_gate="SESSION724_ACTIVE_IN_INVENTORY",
            expected=f"{SESSION_ID}:74:active",
            actual=after["session_inventory"],
        )
    if "729:13:active" not in (after["session_inventory"] or ""):
        raise GateFailure(
            failed_stage=stage,
            failed_gate="ACCOUNT13_SESSION729_STILL_ACTIVE",
            expected="729:13:active",
            actual=after["session_inventory"],
        )
    if "725:23:active" not in (after["session_inventory"] or ""):
        raise GateFailure(
            failed_stage=stage,
            failed_gate="ACCOUNT23_SESSION725_STILL_ACTIVE",
            expected="725:23:active",
            actual=after["session_inventory"],
        )


def host_postcheck_with_auto_rollback(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    inner: dict[str, Any],
    rollback_invoker: Any = None,
    restore_verifier: Any = None,
) -> None:
    """Host postchecks after a committed promotion; auto exact-rollback on failure.

    Success: returns None (caller may emit COMPLETE).
    Failure after commit: raises GateFailure with ROLLBACK_PERFORMED and never COMPLETE.
    """
    try:
        assert_host_postconditions(before=before, after=after, inner=inner)
    except GateFailure as post_fail:
        if promotion_committed(inner):
            raise handle_host_postcheck_failure_after_commit(
                before=before,
                post_failure=post_fail,
                inner=inner,
                rollback_invoker=rollback_invoker,
                restore_verifier=restore_verifier,
            ) from post_fail
        raise


def run_host() -> dict[str, Any]:
    authorize(stage="HOST_AUTHORIZE")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    before = snapshot_inventory()
    backup = fresh_backup()
    backup_verify = verify_backup_before_mutation(backup)
    pre = host_precheck()
    # Re-verify backup immediately before docker mutation
    backup_verify_2 = verify_backup_before_mutation(backup)

    q74 = pre["queue74"]
    env_args = [
        "-e",
        "L8_EXEC_ROLE=worker",
        "-e",
        "L8_ALLOW_PRODUCTION_PROMOTION=1",
        "-e",
        "L8_PRODUCTION_TARGET=account74",
        "-e",
        f"L8_QUEUE74_LEN={q74}",
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
        "scripts/_l8_promote_account74_production.py",
    ]
    started = _run(cmd, check=False)
    out = (started.stdout or "").strip()
    err = (started.stderr or "").strip()
    inner: dict[str, Any] | None = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{") and (
            "ACCOUNT74_PROMOTION_PASS" in line or "FAILED_STAGE" in line
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
        # Worker already handled its own post-commit rollback when applicable.
        raise GateFailure(
            failed_stage=str(inner.get("FAILED_STAGE")),
            failed_gate=str(inner.get("FAILED_GATE")),
            expected=inner.get("EXPECTED"),
            actual=inner.get("ACTUAL"),
            rollback_performed=bool(inner.get("ROLLBACK_PERFORMED")),
            rollback_result=inner.get("ROLLBACK_RESULT"),
        )

    after = snapshot_inventory()
    host_postcheck_with_auto_rollback(before=before, after=after, inner=inner)

    artifact = {
        "phase": "L8_BATCH_A_ACCOUNT74_PROMOTION",
        "PHASE_STATUS": "COMPLETE",
        "backup": backup,
        "backup_verify": backup_verify,
        "backup_verify_immediate_pre_mutation": backup_verify_2,
        "precheck": pre,
        "before": before,
        "after": after,
        "inner": inner,
        "ACCOUNT74_PROMOTION_PASS": True,
        "ACCOUNT74_ACTIVE_SESSION_ID": SESSION_ID,
        "ACCOUNT74_CANONICAL_LOADER_PASS": True,
        "ACCOUNT74_LEGACY_CANONICAL_MATCH": True,
        "GLOBAL_ACTIVE_SESSION_COUNT": after["active_global"],
        "ACCOUNT13_UNCHANGED": True,
        "ACCOUNT23_UNCHANGED": True,
        "ACCOUNT12_UNCHANGED": True,
        "ACCOUNT79_UNCHANGED": True,
        "TOTAL_CHANNEL_SESSION_COUNT_UNCHANGED": True,
        "MESSAGE_SENT": False,
        "OTP_REQUESTED": False,
        "queue74_after": after["queue74"],
        "EXACT_OPERATOR_INPUT_REQUIRED": (
            "Review Account74 before next Batch A step"
        ),
        "CURRENT_PHASE": "L8_BATCH_A_ACCOUNT74_PROMOTION",
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
        if role == "rollback":
            payload = run_rollback_worker()
            print(json.dumps(payload))
            return 0 if payload.get("ok") else 2
        if role != "host":
            raise GateFailure(
                failed_stage="ENTRY",
                failed_gate="L8_EXEC_ROLE",
                expected="host|worker|rollback",
                actual=role,
            )
        artifact = run_host()
        OUT.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        print(json.dumps(artifact, indent=2))
        return 0
    except GateFailure as exc:
        payload = {"ok": False, **exc.as_dict()}
        # Never emit COMPLETE after failed postcheck / rollback path.
        if payload.get("PHASE_STATUS") != "BLOCKED":
            payload["PHASE_STATUS"] = "BLOCKED"
        if exc.rollback_result == "FAIL" or (
            isinstance(exc.rollback_result, str)
            and exc.rollback_result.upper() == "FAIL"
        ):
            payload["MANUAL_INTERVENTION_REQUIRED"] = True
        print(json.dumps(payload, indent=2), file=sys.stderr)
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "L8_ACCOUNT74_LAST_FAILURE.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        # Never emit COMPLETE on failure.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
