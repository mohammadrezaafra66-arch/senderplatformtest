#!/usr/bin/env python3
"""L16 Account27 worker canary: audit -> rollout -> observe -> stop before ENFORCE.

If worker health/stability fails: rollback cohort only.
Never OTP. Never canonical allowlist change. Never send.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports" / "rubika-remediation"
ROLLOUT = ROOT / "scripts" / "_l16_a27_rollout.py"
AUDIT = ROOT / "scripts" / "_l16_a27_worker_subcommand_audit.py"
VERIFY_OUT = ROOT / "scripts" / "_l16_a27_post_login_verify.out.json"
EXPECTED = [12, 13, 23, 27, 74, 79]
ROLLBACK_WORKERS = [12, 13, 23, 74, 79]


def run_py(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(
        [sys.executable, *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if check and p.returncode != 0:
        print(p.stdout)
        print(p.stderr)
        raise SystemExit(p.returncode)
    return p


def parse_json_stdout(p: subprocess.CompletedProcess) -> dict:
    text = (p.stdout or "").strip()
    if not text:
        raise ValueError(f"empty stdout rc={p.returncode} stderr={(p.stderr or '')[-300:]}")
    # Prefer full-document JSON (multiline pretty-print).
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # Fallback: largest {...} block
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        obj = json.loads(text[start : end + 1])
        if isinstance(obj, dict):
            return obj
    raise ValueError(f"no json object in stdout: {text[:300]}")


def sentinel_check() -> dict:
    code = r"""
import asyncio, json
from sqlalchemy import text
from core_engine.database import SessionLocal
from workers.config import get_worker_settings
from workers.redis_keys import worker_account_coverage_key
from workers.session_access import load_account_session_plaintext
from core_engine.models import SessionType
import redis.asyncio as redis

db = SessionLocal()
try:
    db.execute(text("SET TRANSACTION READ ONLY"))
    active = db.execute(text(
        "SELECT id FROM channel_sessions WHERE account_id=27 AND session_status::text='active'"
    )).scalar()
    sess_count = db.execute(text(
        "SELECT count(*) FROM channel_sessions WHERE account_id=27"
    )).scalar()
    global_active = db.execute(text(
        "SELECT count(*) FROM channel_sessions WHERE session_status::text='active'"
    )).scalar()
    msg = db.execute(text("SELECT count(*) FROM message_attempts")).scalar()
    challenges = db.execute(text(
        "SELECT count(*) FROM rubika_login_challenges WHERE account_id=27"
    )).scalar()
    pt = load_account_session_plaintext(db, account_id=27, session_type=SessionType.RUBIKA_SESSION)
    dispatch_ok = isinstance(pt, (bytes, bytearray)) and len(pt) > 0
    del pt
finally:
    db.rollback()
    db.close()

s = get_worker_settings()

async def cov():
    r = redis.from_url(s.REDIS_URL, decode_responses=True)
    await r.ping()
    out = {
        "q27": int(await r.llen("queue:rubika:27")),
        "cov27": bool(await r.exists(worker_account_coverage_key("rubika", 27))),
    }
    await r.aclose()
    return out

c = asyncio.run(cov())
print(json.dumps({
    "active_session_id": int(active) if active else None,
    "session_count": int(sess_count or 0),
    "global_active": int(global_active or 0),
    "msg": int(msg or 0),
    "challenge_count": int(challenges or 0),
    "dispatch_ok": dispatch_ok,
    "canon_mode": s.RUBIKA_CANONICAL_SESSION_MODE,
    "canon_allow": s.RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS,
    "cohort": s.RUBIKA_WORKER_DISCOVERY_COHORT_IDS,
    "pin": getattr(s, "RUBIKA_ACCOUNT_IDS", None),
    "mode": s.RUBIKA_WORKER_DISCOVERY_MODE,
    **c,
}))
"""
    p = subprocess.run(
        [
            "docker",
            "exec",
            "-e",
            "PYTHONPATH=/app",
            "-w",
            "/app",
            "mmp_rubika_worker",
            "python",
            "-c",
            code,
        ],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        return {"ok": False, "stderr": (p.stderr or "")[-500:]}
    return {"ok": True, **json.loads(p.stdout.strip().splitlines()[-1])}


def main() -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)

    # 1) Source audit
    ap = run_py(str(AUDIT), check=False)
    audit = parse_json_stdout(ap)
    print(json.dumps({"audit": audit}, indent=2))
    if not audit.get("ok"):
        print("PHASE_STATUS=BLOCKED audit failed")
        return 2

    # 2) Preconditions from prior verify + live worker probe
    if not VERIFY_OUT.is_file():
        print("PHASE_STATUS=BLOCKED missing post-login verify artifact")
        return 2
    verify = json.loads(VERIFY_OUT.read_text(encoding="utf-8"))
    preconds = {
        "PILOT_ACTIVE_SESSION_ID": verify.get("active_session_id_observed") == 772,
        "PILOT_SESSION_COUNT": verify.get("PILOT_SESSION_COUNT") == 1,
        "PILOT_SHADOW_RESULT": verify.get("PILOT_SHADOW_RESULT") == "SHADOW_MATCH",
        "PILOT_DYNAMIC_ELIGIBLE": verify.get("PILOT_DYNAMIC_ELIGIBLE") is True,
        "reconnect": verify.get("PILOT_RECONNECT_PASS") is True,
        "identity": verify.get("PILOT_IDENTITY_VERIFY_PASS") is True,
        "queue27": verify.get("queue27") == 0,
        "global_active": verify.get("GLOBAL_ACTIVE_SESSION_COUNT") == 4,
        "msg": verify.get("MessageAttempt_count") == 5,
        "message_sent": verify.get("MESSAGE_SENT") is False,
        "verify_ok": verify.get("ok") is True,
    }
    print(json.dumps({"preconditions": preconds}, indent=2))
    if not all(preconds.values()):
        print("PHASE_STATUS=BLOCKED preconditions failed")
        return 2

    probe0 = parse_json_stdout(run_py(str(ROLLOUT), "worker-probe"))
    print(json.dumps({"worker_probe_pre": probe0}, indent=2))
    if not (
        probe0.get("ok")
        and probe0.get("actual") == ROLLBACK_WORKERS
        and probe0.get("mode") == "dynamic"
        and probe0.get("cohort") == [13, 23, 74]
        and probe0.get("pin") == [12, 79]
        and str(probe0.get("canon_mode")).lower() == "enforce"
        and str(probe0.get("canon_allow")).replace(" ", "") == "13,23,74"
    ):
        print("PHASE_STATUS=BLOCKED worker probe pre-state mismatch")
        return 2

    # 3) Execute authorized mutation sequence
    backup = parse_json_stdout(run_py(str(ROLLOUT), "backup-cohort"))
    print(json.dumps({"backup": backup}, indent=2))
    if not backup.get("rollback_hard_gate_pass"):
        print("PHASE_STATUS=BLOCKED rollback hard gate failed")
        return 2
    rollback_path = backup["rollback_file"]

    setc = parse_json_stdout(run_py(str(ROLLOUT), "set-cohort"))
    print(json.dumps({"set_cohort": setc}, indent=2))
    if setc.get("key") != "RUBIKA_WORKER_DISCOVERY_COHORT_IDS" or setc.get("value") != "13,23,27,74":
        print("PHASE_STATUS=BLOCKED unexpected set-cohort result")
        return 2

    recre = parse_json_stdout(run_py(str(ROLLOUT), "recreate-worker"))
    print(json.dumps({"recreate": {"rc": recre.get("rc"), "cmd": recre.get("cmd")}}, indent=2))
    if recre.get("rc") != 0:
        print("PHASE_STATUS=BLOCKED recreate failed — attempting rollback")
        run_py(str(ROLLOUT), "rollback-cohort", rollback_path, check=False)
        return 2

    # wait for worker boot + first refresh
    time.sleep(20)

    # 4) Observe >=3 reconciliation cycles (refresh=60s)
    obs_p = run_py(str(ROLLOUT), "observe", "3", "65", check=False)
    print(obs_p.stdout)
    try:
        observe = parse_json_stdout(obs_p)
    except Exception as exc:  # noqa: BLE001
        observe = {"parse_error": str(exc), "PILOT_WORKER_COVERAGE_STABLE": False}

    # 5) Sentinels via rubika_worker
    sent = sentinel_check()
    print(json.dumps({"sentinels": sent}, indent=2))

    duplicate = False
    if observe.get("observations"):
        # duplicate if more than one coverage or unexpected ids
        for o in observe["observations"]:
            actual = o.get("probe", {}).get("actual")
            if actual and actual.count(27) > 1:
                duplicate = True
            if actual and actual != EXPECTED:
                duplicate = duplicate or False  # mismatch handled by stable flag

    present = observe.get("PILOT_WORKER_PRESENT") is True
    health = observe.get("PILOT_WORKER_HEALTH_PASS") is True
    stable = observe.get("PILOT_WORKER_COVERAGE_STABLE") is True
    workers_ok = observe.get("ACTUAL_WORKER_IDS") == EXPECTED
    sent_ok = (
        sent.get("ok")
        and sent.get("active_session_id") == 772
        and sent.get("session_count") == 1
        and sent.get("global_active") == 4
        and sent.get("msg") == 5
        and sent.get("q27") == 0
        and sent.get("cov27") is True
        and sent.get("dispatch_ok") is True
        and str(sent.get("canon_mode")).lower() == "enforce"
        and str(sent.get("canon_allow")).replace(" ", "") == "13,23,74"
        and str(sent.get("cohort")).replace(" ", "") == "13,23,27,74"
        and str(sent.get("pin")).replace(" ", "") == "12,79"
        and str(sent.get("mode")).lower() == "dynamic"
    )

    rollback_performed = False
    rollback_result = None
    if not (present and health and stable and workers_ok and sent_ok and not duplicate):
        print("WORKER CANARY FAILED — rolling back cohort only")
        rb = run_py(str(ROLLOUT), "rollback-cohort", rollback_path, check=False)
        time.sleep(20)
        probe_rb = parse_json_stdout(run_py(str(ROLLOUT), "worker-probe", check=False))
        rollback_performed = True
        rollback_result = {
            "restore_stdout_tail": (rb.stdout or "")[-800:],
            "probe": probe_rb,
            "recovered": probe_rb.get("actual") == ROLLBACK_WORKERS,
        }
        final = {
            "WORKER_ROLLOUT_MUTATION_SCOPE_EXACT": audit.get(
                "WORKER_ROLLOUT_MUTATION_SCOPE_EXACT"
            ),
            "CANONICAL_CONFIG_MUTATED_BY_WORKER_SUBCOMMANDS": audit.get(
                "CANONICAL_CONFIG_MUTATED_BY_WORKER_SUBCOMMANDS"
            ),
            "PILOT_ACTIVE_SESSION_ID": 772,
            "PILOT_DYNAMIC_ELIGIBLE": True,
            "PILOT_WORKER_PRESENT": present,
            "PILOT_WORKER_HEALTH_PASS": health,
            "PILOT_WORKER_DUPLICATE": duplicate,
            "PILOT_WORKER_COVERAGE_STABLE": stable,
            "ACTUAL_WORKER_IDS": observe.get("ACTUAL_WORKER_IDS"),
            "WORKER_DISCOVERY_COHORT": sent.get("cohort"),
            "CANONICAL_MODE": "enforce",
            "CANONICAL_ALLOWLIST": "13,23,74",
            "GLOBAL_ACTIVE_SESSION_COUNT": sent.get("global_active"),
            "MESSAGE_SENT": False,
            "OTP_REQUESTED_AGAIN": False,
            "ROLLBACK_PERFORMED": True,
            "ROLLBACK_RESULT": rollback_result,
            "CURRENT_PHASE": "L16_ACCOUNT27_WORKER_CANARY",
            "PHASE_STATUS": "BLOCKED",
            "observe": observe,
            "sentinels": sent,
        }
        (REPORTS / "L16_A27_WORKER_CANARY.json").write_text(
            json.dumps(final, indent=2), encoding="utf-8"
        )
        print(json.dumps(final, indent=2))
        return 2

    final = {
        "WORKER_ROLLOUT_MUTATION_SCOPE_EXACT": True,
        "CANONICAL_CONFIG_MUTATED_BY_WORKER_SUBCOMMANDS": False,
        "WORKER_PROBE_READ_ONLY": audit.get("WORKER_PROBE_READ_ONLY"),
        "BACKUP_COHORT_SCOPE_SAFE": audit.get("BACKUP_COHORT_SCOPE_SAFE"),
        "SET_COHORT_TARGET_EXACT": audit.get("SET_COHORT_TARGET_EXACT"),
        "RECREATE_WORKER_SCOPE_EXACT": audit.get("RECREATE_WORKER_SCOPE_EXACT"),
        "PILOT_ACTIVE_SESSION_ID": 772,
        "PILOT_DYNAMIC_ELIGIBLE": True,
        "PILOT_WORKER_PRESENT": True,
        "PILOT_WORKER_HEALTH_PASS": True,
        "PILOT_WORKER_DUPLICATE": False,
        "PILOT_WORKER_COVERAGE_STABLE": True,
        "ACTUAL_WORKER_IDS": EXPECTED,
        "WORKER_DISCOVERY_COHORT": "13,23,27,74",
        "CANONICAL_MODE": "enforce",
        "CANONICAL_ALLOWLIST": "13,23,74",
        "GLOBAL_ACTIVE_SESSION_COUNT": 4,
        "MESSAGE_SENT": False,
        "OTP_REQUESTED_AGAIN": False,
        "ROLLBACK_PERFORMED": False,
        "ROLLBACK_RESULT": None,
        "rollback_copy": rollback_path,
        "CURRENT_PHASE": "L16_ACCOUNT27_WORKER_CANARY",
        "PHASE_STATUS": "COMPLETE",
        "EXACT_OPERATOR_INPUT_REQUIRED": "Proceed with Account27 canonical ENFORCE rollout",
        "observe": observe,
        "sentinels": sent,
        "STOPPED_BEFORE_CANONICAL_ENFORCE": True,
    }
    (REPORTS / "L16_A27_WORKER_CANARY.json").write_text(
        json.dumps(final, indent=2), encoding="utf-8"
    )
    print(json.dumps(final, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
