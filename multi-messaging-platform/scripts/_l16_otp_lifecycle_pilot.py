#!/usr/bin/env python3
"""L16 — End-to-end Rubika OTP lifecycle pilot orchestration.

Read-only subcommands (no production mutation):
  select  — classify candidates; write secret-safe selection artifact
  audit   — source + runtime flag inspection; optional pilot routing proof

Mutating subcommands (explicit; not invoked by select/audit):
  deploy-pilot-gate, backup, baseline, prepare-pre-otp, request-otp
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "rubika-remediation"
SELECTION_ARTIFACT = REPORT_DIR / "L16_PILOT_SELECTION.json"
OVERRIDE = ROOT / "docker-compose.override.yml"
PG = "mmp_postgres"
PG_USER = "mmp_user"
PRODUCTION_DB = "mmp_db"
CORE_API = "mmp_core_api"
WORKER = "mmp_rubika_worker"
EXCLUDED = frozenset({2, 12, 13, 23, 74, 79})
ACCOUNTS_PY = ROOT / "core_engine" / "api" / "accounts.py"
STATE_MACHINE_PY = ROOT / "core_engine" / "services" / "rubika_login_state_machine.py"


def _run(cmd: list[str], *, check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check, cwd=str(cwd or ROOT))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mask_phone(phone: str | None) -> str:
    if not phone:
        return "***"
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if len(digits) < 6:
        return "***"
    return f"{digits[:3]}***{digits[-3:]}"


def _running_inside_container() -> bool:
    import os

    return Path("/.dockerenv").is_file() or os.environ.get("HOSTNAME", "").startswith("mmp_")


def _docker_env(container: str, name: str) -> str:
    import os

    if _running_inside_container() and name in os.environ:
        return os.environ.get(name, "")
    r = _run(["docker", "exec", container, "printenv", name], check=False)
    return (r.stdout or "").strip()


def _source_login_routing_audit() -> dict:
    """Read-only inspection of authoritative API routing source."""
    accounts_src = ACCOUNTS_PY.read_text(encoding="utf-8") if ACCOUNTS_PY.is_file() else ""
    sm_src = STATE_MACHINE_PY.read_text(encoding="utf-8") if STATE_MACHINE_PY.is_file() else ""
    return {
        "accounts_register_uses_account_uses_l3_login": "account_uses_l3_login(account_id)" in accounts_src,
        "accounts_register_calls_request_rubika_login": "request_rubika_login(" in accounts_src,
        "accounts_verify_uses_account_uses_l3_login": accounts_src.count("account_uses_l3_login(account_id)") >= 2,
        "accounts_verify_calls_submit_rubika_login_code": "submit_rubika_login_code(" in accounts_src,
        "state_machine_has_account_uses_l3_login": "def account_uses_l3_login(" in sm_src,
        "state_machine_has_pilot_account_ids_config": "RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS" in sm_src,
        "legacy_still_gated_per_account": "assert_legacy_login_allowed(account_id)" in (
            ROOT / "core_engine" / "services" / "rubika_user_session.py"
        ).read_text(encoding="utf-8"),
    }


def audit_login_path(*, pilot_id: int | None = None) -> dict:
    """Strictly read-only: env flags + source routing audit + optional pilot proof."""
    v1 = _docker_env(CORE_API, "RUBIKA_CANONICAL_SESSION_V1").lower()
    pilot_env = _docker_env(CORE_API, "RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS")
    mode = _docker_env(CORE_API, "RUBIKA_CANONICAL_SESSION_MODE")
    user_login = _docker_env(CORE_API, "RUBIKA_USER_ACCOUNT_ENABLED").lower()
    delivery = _docker_env(CORE_API, "RUBIKA_DELIVERY_MODE")
    global_l3 = v1 in {"1", "true", "yes", "on"}
    source = _source_login_routing_audit()
    source_ok = all(
        [
            source["accounts_register_uses_account_uses_l3_login"],
            source["accounts_register_calls_request_rubika_login"],
            source["accounts_verify_uses_account_uses_l3_login"],
            source["accounts_verify_calls_submit_rubika_login_code"],
            source["state_machine_has_account_uses_l3_login"],
        ]
    )
    routing: dict | None = None
    pilot_l3 = False
    if pilot_id is not None:
        routing = verify_l3_routing(int(pilot_id))
        pilot_l3 = bool(routing.get("pilot_l3"))
    elif pilot_env.strip().isdigit():
        routing = verify_l3_routing(int(pilot_env.strip()))
        pilot_l3 = bool(routing.get("pilot_l3"))
    else:
        pilot_l3 = global_l3

    # Secret-safe: never include DATABASE_URL, REDIS_URL, phones, OTP, session blobs.
    out = {
        "L16_AUDIT_READ_ONLY": True,
        "L16_AUDIT_SECRET_SAFE": True,
        "RUBIKA_CANONICAL_SESSION_V1": v1 or "false",
        "RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS": pilot_env or "",
        "RUBIKA_CANONICAL_SESSION_MODE": mode,
        "RUBIKA_USER_ACCOUNT_ENABLED": user_login,
        "RUBIKA_DELIVERY_MODE": delivery,
        "global_l3": global_l3,
        "pilot_ids_configured": [
            int(x) for x in pilot_env.split(",") if x.strip().isdigit()
        ],
        "source_routing": source,
        "source_routing_ok": source_ok,
        "runtime_routing_proof": routing,
        "PILOT_USES_L3_LOGIN_STATE_MACHINE": bool(
            source_ok and (pilot_l3 if pilot_id or pilot_env.strip() else global_l3)
        ),
    }
    if pilot_id is not None:
        out["pilot_id_audited"] = int(pilot_id)
    return out


def select_pilot_account() -> dict:
    """Strictly read-only deterministic pilot selection."""
    code = r'''
import asyncio, json
from sqlalchemy import text
from core_engine.database import SessionLocal
from core_engine.models import Account, AccountStatus, PlatformType, RubikaAccountPool
from workers.config import get_worker_settings
from workers.rubika_account_pool import resolve_current_phase
import redis.asyncio as redis

EXCLUDED = {2, 12, 13, 23, 74, 79}
ACTIVE_STATES = (
    "otp_requested", "otp_waiting_for_operator", "otp_submitted",
    "authenticating", "identity_verifying", "session_persisting",
)
ACTIVE_CAMPAIGN = ("running", "prepared", "paused")

def mask(p):
    if not p: return "***"
    d = "".join(c for c in str(p) if c.isdigit())
    return f"{d[:3]}***{d[-3:]}" if len(d) >= 6 else "***"

async def queue_len(redis_url, aid):
    r = redis.from_url(redis_url, decode_responses=True)
    try:
        await r.ping()
        return int(await r.llen(f"queue:rubika:{aid}"))
    finally:
        await r.aclose()

db = SessionLocal()
try:
    db.execute(text("SET TRANSACTION READ ONLY"))
    phase = resolve_current_phase(db)
    pool_ids = set()
    if phase:
        pool_ids = {int(r.account_id) for r in db.query(RubikaAccountPool).filter(
            RubikaAccountPool.phase == phase).all()}
    accounts = (
        db.query(Account)
        .filter(Account.platform == PlatformType.RUBIKA)
        .order_by(Account.id.asc())
        .all()
    )
    s = get_worker_settings()
    redis_url = s.REDIS_URL
    candidates = []
    for account in accounts:
        aid = int(account.id)
        r_status = account.status.value if hasattr(account.status, "value") else str(account.status or "")
        sess_n = db.execute(
            text("SELECT count(*) FROM channel_sessions WHERE account_id=:a"), {"a": aid}
        ).scalar() or 0
        active_ch = db.execute(
            text(
                "SELECT count(*) FROM rubika_login_challenges "
                "WHERE account_id=:a AND state::text = ANY(:active)"
            ),
            {"a": aid, "active": list(ACTIVE_STATES)},
        ).scalar() or 0
        active_camp = db.execute(
            text(
                "SELECT count(*) FROM campaign_accounts ca "
                "JOIN campaigns c ON c.id=ca.campaign_id "
                "WHERE ca.account_id=:a AND c.status::text = ANY(:camp)"
            ),
            {"a": aid, "camp": list(ACTIVE_CAMPAIGN)},
        ).scalar() or 0
        reasons = []
        if aid in EXCLUDED:
            reasons.append("EXCLUDED_ID")
        if r_status != AccountStatus.ACTIVE.value:
            reasons.append(f"STATUS_{r_status}")
        if int(sess_n) != 0:
            reasons.append("HAS_SESSION")
        if int(active_ch) > 0:
            reasons.append("ACTIVE_CHALLENGE")
        if int(active_camp) > 0:
            reasons.append("ACTIVE_CAMPAIGN")
        phone = (account.phone_number or "").strip()
        if not phone:
            reasons.append("NO_PHONE")
        guid = (account.rubika_guid or "").strip()
        if guid:
            reasons.append("IDENTITY_ALREADY_BOUND")
        in_pool = aid in pool_ids if phase else False
        if phase and not in_pool:
            reasons.append("NOT_IN_POOL")
        if phase is None:
            reasons.append("NO_SCHEDULE")
        q = asyncio.run(queue_len(redis_url, aid)) if not reasons else -1
        if q != -1 and q != 0:
            reasons.append(f"QUEUE_{q}")
        score = 0
        if in_pool:
            score += 100
        if phone.startswith("98"):
            score += 10
        candidates.append({
            "account_id": aid,
            "phone_masked": mask(phone),
            "label": (account.label or "")[:40],
            "phase": phase,
            "in_pool": in_pool,
            "queue": q if q >= 0 else None,
            "selection_score": score,
            "eligible": not reasons,
            "blockers": reasons,
        })
    eligible = sorted(
        [c for c in candidates if c["eligible"]],
        key=lambda x: (-x["selection_score"], x["account_id"]),
    )
    chosen = eligible[0] if eligible else None
    artifact = {
        "L16_SELECT_READ_ONLY": True,
        "L16_SELECT_SECRET_SAFE": True,
        "phase": phase,
        "pool_size": len(pool_ids),
        "rubika_account_count": len(accounts),
        "eligible_count": len(eligible),
        "PILOT_ACCOUNT_ID": chosen["account_id"] if chosen else None,
        "PILOT_ACCOUNT_PHONE_MASKED": chosen["phone_masked"] if chosen else None,
        "selection": {k: v for k, v in (chosen or {}).items() if k != "phone_masked"} if chosen else None,
        "eligible_preview": [
            {k: v for k, v in c.items() if k != "phone_masked"} for c in eligible[:5]
        ],
    }
    print(json.dumps(artifact, default=str))
finally:
    db.rollback()
    db.close()
'''
    r = _run(
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
            code,
        ]
    )
    data = json.loads(r.stdout.strip())
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    SELECTION_ARTIFACT.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return data


def verify_db_backup(meta: dict) -> dict:
    path = Path(str(meta.get("BACKUP_PATH") or ""))
    if not path.is_file() or path.stat().st_size <= 0:
        return {"PRE_OTP_BACKUP_PASS": False, "reason": "missing_or_empty"}
    digest = _sha256(path)
    ok = (
        digest == str(meta.get("BACKUP_SHA256") or "")
        and str(meta.get("source_db") or "") == PRODUCTION_DB
    )
    return {
        "PRE_OTP_BACKUP_PASS": ok,
        "BACKUP_PATH": str(path),
        "BACKUP_SHA256": digest,
        "BACKUP_SIZE": path.stat().st_size,
        "source_db": meta.get("source_db"),
        "sha_match": digest == str(meta.get("BACKUP_SHA256") or ""),
    }


def verify_pilot_preconditions(pilot_id: int) -> dict:
    """Read-only final gate before OTP request."""
    code = f'''
import asyncio, json
from sqlalchemy import text
from core_engine.database import SessionLocal
from core_engine.models import Account
from workers.config import get_worker_settings
import redis.asyncio as redis

PILOT = {int(pilot_id)}
ACTIVE = (
    "otp_requested", "otp_waiting_for_operator", "otp_submitted",
    "authenticating", "identity_verifying", "session_persisting",
)
ACTIVE_CAMPAIGN = ("running", "prepared", "paused")
db = SessionLocal()
try:
    db.execute(text("SET TRANSACTION READ ONLY"))
    sess = int(db.execute(text(
        "SELECT count(*) FROM channel_sessions WHERE account_id=:a"), {{"a": PILOT}}).scalar() or 0)
    active_sess = int(db.execute(text(
        "SELECT count(*) FROM channel_sessions WHERE account_id=:a "
        "AND session_status::text='active'"), {{"a": PILOT}}).scalar() or 0)
    active_ch = int(db.execute(text(
        "SELECT count(*) FROM rubika_login_challenges WHERE account_id=:a AND state::text = ANY(:s)"
    ), {{"a": PILOT, "s": list(ACTIVE)}}).scalar() or 0)
    active_camp = int(db.execute(text(
        "SELECT count(*) FROM campaign_accounts ca JOIN campaigns c ON c.id=ca.campaign_id "
        "WHERE ca.account_id=:a AND c.status::text = ANY(:camp)"
    ), {{"a": PILOT, "camp": list(ACTIVE_CAMPAIGN)}}).scalar() or 0)
    acct = db.query(Account).filter(Account.id == PILOT).first()
    guid = (acct.rubika_guid or "").strip() if acct else ""
finally:
    db.rollback()
    db.close()
s = get_worker_settings()
async def q():
    r = redis.from_url(s.REDIS_URL, decode_responses=True)
    try:
        await r.ping()
        return int(await r.llen(f"queue:rubika:{{PILOT}}"))
    finally:
        await r.aclose()
qlen = asyncio.run(q())
print(json.dumps({{
    "PILOT_SESSION_COUNT_BEFORE": sess,
    "PILOT_ACTIVE_SESSION_COUNT_BEFORE": active_sess,
    "PILOT_ACTIVE_LOGIN_CHALLENGE_COUNT": active_ch,
    "PILOT_QUEUE": qlen,
    "PILOT_IDENTITY_CONFLICT": bool(guid),
    "PILOT_ACTIVE_CAMPAIGN": active_camp > 0,
}}, default=str))
'''
    r = _run(
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
            code,
        ]
    )
    return json.loads(r.stdout.strip())


def record_baseline(pilot_id: int | None) -> dict:
    code = f'''
import json, asyncio
from sqlalchemy import text
from core_engine.database import SessionLocal
from workers.config import get_worker_settings
from workers.rubika_pool_worker import resolve_rubika_worker_account_ids_from_settings
import redis.asyncio as redis

PILOT = {pilot_id!r}
db = SessionLocal()
try:
    db.execute(text("SET TRANSACTION READ ONLY"))
    total_sess = int(db.execute(text("SELECT count(*) FROM channel_sessions")).scalar() or 0)
    active_g = int(db.execute(text("SELECT count(*) FROM channel_sessions WHERE session_status::text='active'")).scalar() or 0)
    pilot_sess = 0
    if PILOT:
        pilot_sess = int(db.execute(text("SELECT count(*) FROM channel_sessions WHERE account_id=:a"), {{"a": PILOT}}).scalar() or 0)
    login = int(db.execute(text("SELECT count(*) FROM rubika_login_challenges")).scalar() or 0)
    msg = int(db.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0)
finally:
    db.rollback()
    db.close()
s = get_worker_settings()
ids, meta = resolve_rubika_worker_account_ids_from_settings(s)
async def cov(a):
    r = redis.from_url(s.REDIS_URL, decode_responses=True)
    await r.ping()
    q = int(await r.llen(f"queue:rubika:{{a}}"))
    await r.aclose()
    return q
pilot_q = asyncio.run(cov(PILOT)) if PILOT else 0
print(json.dumps({{
    "TOTAL_CHANNEL_SESSION_COUNT": total_sess,
    "GLOBAL_ACTIVE_SESSION_COUNT": active_g,
    "pilot_session_count": pilot_sess,
    "LoginChallenge_count": login,
    "MessageAttempt_count": msg,
    "pilot_queue": pilot_q,
    "actual_workers": ids,
    "canonical_mode": s.RUBIKA_CANONICAL_SESSION_MODE,
    "canonical_allowlist": s.RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS,
    "discovery_mode": meta["mode"],
    "cohort": meta["cohort_ids"],
    "pin": meta["pinned_ids"],
}}, default=str))
'''
    r = _run(
        [
            "docker",
            "exec",
            "-e",
            "PYTHONPATH=/app",
            "-w",
            "/app",
            WORKER,
            "python",
            "-c",
            code,
        ]
    )
    return json.loads(r.stdout.strip())


def fresh_backup() -> dict:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump = REPORT_DIR / f"mmp_db_pre_l16_otp_{ts}.dump"
    _run(
        [
            "docker",
            "exec",
            PG,
            "pg_dump",
            "-U",
            PG_USER,
            "-Fc",
            "-f",
            f"/tmp/{dump.name}",
            PRODUCTION_DB,
        ]
    )
    _run(["docker", "cp", f"{PG}:/tmp/{dump.name}", str(dump)])
    digest = _sha256(dump)
    meta = {
        "BACKUP_PATH": str(dump.resolve()),
        "BACKUP_SHA256": digest,
        "BACKUP_SIZE": dump.stat().st_size,
        "created_at": ts,
        "source_db": PRODUCTION_DB,
    }
    meta_path = dump.with_suffix(".dump.meta.json")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def backup_override() -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rb = ROOT / f"docker-compose.override.yml.before-l16-otp-{ts}.bak"
    rb.write_bytes(OVERRIDE.read_bytes())
    return {"path": str(rb), "sha256": _sha256(rb), "size": rb.stat().st_size}


def set_pilot_login_env(pilot_id: int) -> dict:
    text = OVERRIDE.read_text(encoding="utf-8")
    key = "RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS"
    if re.search(rf"^\s*{re.escape(key)}:", text, flags=re.M):
        text2, n = re.subn(
            rf'^(\s*{re.escape(key)}:\s*")[^"]*("\s*)$',
            rf'\g<1>{pilot_id}\g<2>',
            text,
            flags=re.M,
        )
        if n < 1:
            raise SystemExit("pilot env replace failed")
    else:
        needle = "  core_api:\n    environment:"
        if needle not in text:
            raise SystemExit("core_api block not found")
        text2 = text.replace(
            needle,
            needle + f'\n      {key}: "{pilot_id}"',
            1,
        )
    OVERRIDE.write_text(text2, encoding="utf-8")
    return {"pilot_id": pilot_id, "sha256": _sha256(OVERRIDE)}


def recreate_core_api() -> None:
    _run(["docker", "compose", "up", "-d", "--force-recreate", "--no-deps", "core_api"])


def core_api_health() -> dict:
    r = _run(["docker", "inspect", "-f", "{{.State.Status}}", CORE_API], check=False)
    status = (r.stdout or "").strip()
    ping = _run(
        [
            "docker",
            "exec",
            CORE_API,
            "python",
            "-c",
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5); print('ok')",
        ],
        check=False,
    )
    return {
        "container_status": status,
        "health_probe": (ping.stdout or "").strip() == "ok",
        "healthy": status == "running" and (ping.stdout or "").strip() == "ok",
    }


def verify_l3_routing(pilot_id: int) -> dict:
    code = f'''
import json
from core_engine.config import get_settings
from core_engine.services.rubika_login_state_machine import account_uses_l3_login

get_settings.cache_clear()
print(json.dumps({{
    "pilot_id": {pilot_id},
    "pilot_l3": account_uses_l3_login({pilot_id}),
    "account13_l3": account_uses_l3_login(13),
    "account79_l3": account_uses_l3_login(79),
    "global_v1": bool(get_settings().RUBIKA_CANONICAL_SESSION_V1),
    "pilot_env": get_settings().RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS,
}}))
'''
    r = _run(
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
            code,
        ]
    )
    data = json.loads(r.stdout.strip())
    data["PILOT_USES_L3_LOGIN_STATE_MACHINE"] = bool(data.get("pilot_l3"))
    return data


def request_otp(pilot_id: int) -> dict:
    code = f'''
import asyncio, json
from core_engine.database import SessionLocal
from core_engine.services.rubika_login_state_machine import request_rubika_login
from core_engine.services.rubika_login_live_provider import LiveRubikaLoginProvider
from core_engine.models import ChannelSession, RubikaLoginChallenge
from sqlalchemy import text

async def main():
    db = SessionLocal()
    try:
        before_sess = db.query(ChannelSession).filter(ChannelSession.account_id=={pilot_id}).count()
        result = await request_rubika_login(
            db, {pilot_id}, provider=LiveRubikaLoginProvider())
        if result.ok:
            db.commit()
        else:
            db.rollback()
        after_sess = db.query(ChannelSession).filter(ChannelSession.account_id=={pilot_id}).count()
        active = db.query(RubikaLoginChallenge).filter(
            RubikaLoginChallenge.account_id=={pilot_id},
            RubikaLoginChallenge.state.in_([
                "otp_requested","otp_waiting_for_operator","otp_submitted",
                "authenticating","identity_verifying","session_persisting",
            ])).count()
        otp_in_db = db.execute(text(
            "SELECT count(*) FROM rubika_login_challenges WHERE account_id=:a "
            "AND (otp_code IS NOT NULL AND otp_code <> '')"
        ), {{"a": {pilot_id}}}).scalar()
        print(json.dumps({{
            "ok": result.ok,
            "code": result.code,
            "challenge_id": result.challenge_id,
            "state": result.state,
            "before_sessions": before_sess,
            "after_sessions": after_sess,
            "active_challenges": active,
            "otp_plaintext_persisted": int(otp_in_db or 0) > 0,
        }}, default=str))
    finally:
        db.close()

asyncio.run(main())
'''
    r = _run(
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
            code,
        ],
        check=False,
    )
    if r.returncode != 0:
        return {"ok": False, "stderr": r.stderr[-500:] if r.stderr else "", "stdout": r.stdout}
    return json.loads(r.stdout.strip())


def verify_batch_a_unchanged() -> dict:
    """Read-only verify Batch A authoritative sessions and worker topology."""
    code = r'''
import json
from sqlalchemy import text
from core_engine.database import SessionLocal
from workers.config import get_worker_settings
from workers.rubika_pool_worker import resolve_rubika_worker_account_ids_from_settings

BATCH = {13: 729, 23: 725, 74: 724}
db = SessionLocal()
try:
    db.execute(text("SET TRANSACTION READ ONLY"))
    actives = {}
    for aid, sid in BATCH.items():
        row = db.execute(text(
            "SELECT id FROM channel_sessions WHERE account_id=:a AND session_status::text='active'"
        ), {"a": aid}).scalar()
        actives[str(aid)] = int(row) if row else None
    global_active = int(db.execute(text(
        "SELECT count(*) FROM channel_sessions WHERE session_status::text='active'"
    )).scalar() or 0)
    msg = int(db.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0)
finally:
    db.rollback()
    db.close()
s = get_worker_settings()
ids, meta = resolve_rubika_worker_account_ids_from_settings(s)
ok = actives == {"13": 729, "23": 725, "74": 724} and ids == [12, 13, 23, 74, 79]
print(json.dumps({
    "batch_a_actives": actives,
    "GLOBAL_ACTIVE_SESSION_COUNT": global_active,
    "MessageAttempt_count": msg,
    "ACTUAL_WORKER_IDS": ids,
    "canonical_mode": s.RUBIKA_CANONICAL_SESSION_MODE,
    "canonical_allowlist": s.RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS,
    "discovery_mode": meta["mode"],
    "cohort": meta["cohort_ids"],
    "pin": meta["pinned_ids"],
    "batch_a_unchanged": ok,
}, default=str))
'''
    r = _run(
        [
            "docker",
            "exec",
            "-e",
            "PYTHONPATH=/app",
            "-w",
            "/app",
            WORKER,
            "python",
            "-c",
            code,
        ]
    )
    return json.loads(r.stdout.strip())


def _pilot_phone_masked_readonly(pilot_id: int) -> str:
    code = f'''
from core_engine.database import SessionLocal
from core_engine.models import Account
db = SessionLocal()
try:
    acct = db.query(Account).filter(Account.id == {int(pilot_id)}).first()
    phone = (acct.phone_number or "").strip() if acct else ""
finally:
    db.close()
digits = "".join(c for c in phone if c.isdigit())
print(f"{{digits[:3]}}***{{digits[-3:]}}" if len(digits) >= 6 else "***")
'''
    r = _run(
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
            code,
        ]
    )
    return (r.stdout or "").strip() or "***"


def prepare_pre_otp(pilot_id: int) -> dict:
    """Safe pre-OTP preparation — stops before OTP request.

    Safety contract (no OTP/session/challenge mutation):
      PREPARE_PRE_OTP_REQUESTS_OTP=False
      PREPARE_PRE_OTP_CREATES_LOGIN_CHALLENGE=False
      PREPARE_PRE_OTP_CREATES_SESSION=False
    """
    sel = select_pilot_account()
    phone_masked = (
        sel.get("PILOT_ACCOUNT_PHONE_MASKED")
        if sel.get("PILOT_ACCOUNT_ID") == pilot_id
        else _pilot_phone_masked_readonly(pilot_id)
    )

    audit_before = audit_login_path(pilot_id=pilot_id)
    config_changed = False
    override_backup = None
    if not audit_before.get("PILOT_USES_L3_LOGIN_STATE_MACHINE"):
        override_backup = backup_override()
        set_pilot_login_env(pilot_id)
        config_changed = True
        recreate_core_api()
        import time

        time.sleep(12)

    health = core_api_health()
    audit_after = audit_login_path(pilot_id=pilot_id)
    routing = verify_l3_routing(pilot_id)
    pre = verify_pilot_preconditions(pilot_id)
    baseline = record_baseline(pilot_id)
    batch_a = verify_batch_a_unchanged()
    db_backup = fresh_backup()
    backup_verify = verify_db_backup(db_backup)

    gates_pass = (
        health.get("healthy")
        and audit_after.get("PILOT_USES_L3_LOGIN_STATE_MACHINE")
        and routing.get("pilot_l3")
        and routing.get("account13_l3") is False
        and routing.get("account79_l3") is False
        and pre["PILOT_SESSION_COUNT_BEFORE"] == 0
        and pre.get("PILOT_ACTIVE_SESSION_COUNT_BEFORE", 0) == 0
        and pre["PILOT_ACTIVE_LOGIN_CHALLENGE_COUNT"] == 0
        and pre["PILOT_QUEUE"] == 0
        and not pre.get("PILOT_IDENTITY_CONFLICT")
        and not pre.get("PILOT_ACTIVE_CAMPAIGN")
        and backup_verify.get("PRE_OTP_BACKUP_PASS")
        and batch_a.get("batch_a_unchanged")
        and batch_a.get("GLOBAL_ACTIVE_SESSION_COUNT") == 3
        and batch_a.get("canonical_mode") == "enforce"
        and batch_a.get("canonical_allowlist") == "13,23,74"
        and batch_a.get("cohort") == [13, 23, 74]
    )

    artifact = {
        "CURRENT_PHASE": "L16_END_TO_END_OTP_LIFECYCLE_PILOT",
        "PHASE_STATUS": "READY_TO_REQUEST_OTP" if gates_pass else "BLOCKED",
        "PILOT_ACCOUNT_ID": pilot_id,
        "PILOT_ACCOUNT_PHONE_MASKED": phone_masked,
        "PILOT_USES_L3_LOGIN_STATE_MACHINE": bool(audit_after.get("PILOT_USES_L3_LOGIN_STATE_MACHINE")),
        "PILOT_SESSION_COUNT_BEFORE": pre["PILOT_SESSION_COUNT_BEFORE"],
        "PILOT_ACTIVE_SESSION_COUNT_BEFORE": pre.get("PILOT_ACTIVE_SESSION_COUNT_BEFORE", 0),
        "PILOT_ACTIVE_LOGIN_CHALLENGE_COUNT": pre["PILOT_ACTIVE_LOGIN_CHALLENGE_COUNT"],
        "PILOT_QUEUE": pre["PILOT_QUEUE"],
        "PILOT_IDENTITY_CONFLICT": pre.get("PILOT_IDENTITY_CONFLICT"),
        "PILOT_ACTIVE_CAMPAIGN": pre.get("PILOT_ACTIVE_CAMPAIGN"),
        "PRE_OTP_BACKUP_PASS": backup_verify.get("PRE_OTP_BACKUP_PASS"),
        "L16_PRE_OTP_GATES_PASS": gates_pass,
        "PREPARE_PRE_OTP_REQUESTS_OTP": False,
        "PREPARE_PRE_OTP_CREATES_LOGIN_CHALLENGE": False,
        "PREPARE_PRE_OTP_CREATES_SESSION": False,
        "OTP_REQUESTED": False,
        "MESSAGE_SENT": False,
        "config_changed": config_changed,
        "override_backup": override_backup,
        "db_backup": db_backup,
        "backup_verify": backup_verify,
        "baseline": baseline,
        "batch_a": batch_a,
        "audit_before": audit_before,
        "audit_after": audit_after,
        "routing": routing,
        "health": health,
        "selection": {"PILOT_ACCOUNT_ID": sel.get("PILOT_ACCOUNT_ID"), "eligible_count": sel.get("eligible_count")},
        "EXACT_OPERATOR_INPUT_REQUIRED": (
            "Approve exactly one OTP request for Account3"
            if gates_pass
            else None
        ),
    }
    out = REPORT_DIR / "L16_OTP_LIFECYCLE_PILOT.json"
    out.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    return artifact


def main() -> int:
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "help").strip().lower()
    if cmd == "audit":
        pid = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
        print(json.dumps(audit_login_path(pilot_id=pid), indent=2))
        return 0
    if cmd == "select":
        print(json.dumps(select_pilot_account(), indent=2))
        return 0
    if cmd == "baseline":
        pid = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        print(json.dumps(record_baseline(pid or None), indent=2))
        return 0
    if cmd == "backup":
        out = {"db": fresh_backup(), "override": backup_override()}
        print(json.dumps(out, indent=2))
        return 0
    if cmd == "deploy-pilot-gate":
        pid = int(sys.argv[2])
        set_pilot_login_env(pid)
        recreate_core_api()
        import time

        time.sleep(8)
        route = verify_l3_routing(pid)
        print(json.dumps(route, indent=2))
        return 0 if route.get("PILOT_USES_L3_LOGIN_STATE_MACHINE") else 2
    if cmd == "prepare-pre-otp":
        pid = int(sys.argv[2])
        artifact = prepare_pre_otp(pid)
        print(json.dumps({k: artifact[k] for k in (
            "PILOT_ACCOUNT_ID", "PILOT_ACCOUNT_PHONE_MASKED",
            "PILOT_USES_L3_LOGIN_STATE_MACHINE", "PILOT_SESSION_COUNT_BEFORE",
            "PILOT_ACTIVE_SESSION_COUNT_BEFORE",
            "PILOT_ACTIVE_LOGIN_CHALLENGE_COUNT", "PILOT_QUEUE",
            "PRE_OTP_BACKUP_PASS", "L16_PRE_OTP_GATES_PASS",
            "PREPARE_PRE_OTP_REQUESTS_OTP", "PREPARE_PRE_OTP_CREATES_LOGIN_CHALLENGE",
            "PREPARE_PRE_OTP_CREATES_SESSION",
            "OTP_REQUESTED", "MESSAGE_SENT",
            "CURRENT_PHASE", "PHASE_STATUS", "EXACT_OPERATOR_INPUT_REQUIRED",
        )}, indent=2))
        return 0 if artifact.get("L16_PRE_OTP_GATES_PASS") else 2
    if cmd == "request-otp":
        pid = int(sys.argv[2])
        print(json.dumps(request_otp(pid), indent=2))
        return 0
    print(
        "usage: audit [pilot_id] | select | baseline ID | backup | "
        "deploy-pilot-gate ID | prepare-pre-otp ID | request-otp ID"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
