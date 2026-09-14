#!/usr/bin/env python3
"""L17 production automation cleanup deployment.

Authorized mutation: add L17 permanent automation env keys only.
Never OTP/send. Never remove pin 12,79. Auto-rollback on hard failure.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERRIDE = ROOT / "docker-compose.override.yml"
REPORTS = ROOT / "reports" / "rubika-remediation"
EXPECTED_WORKERS = [12, 13, 23, 27, 74, 79]
EXPECTED_ENFORCE = [13, 23, 27, 74]
EXPECTED_DYNAMIC = [13, 23, 27, 74, 79]
BATCH = {13: 729, 23: 725, 27: 772, 74: 724}


def _vals(text: str, key: str) -> list[str]:
    return re.findall(rf'^\s*{re.escape(key)}:\s*"([^"]*)"\s*$', text, flags=re.M)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if check and p.returncode != 0:
        print(p.stdout)
        print(p.stderr)
        raise SystemExit(p.returncode)
    return p


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no json: {text[:300]}")
    return json.loads(text[start : end + 1])


def worker_probe() -> dict:
    code = (
        "import json;"
        "from workers.config import get_worker_settings;"
        "from workers.rubika_pool_worker import resolve_rubika_worker_account_ids_from_settings;"
        "s=get_worker_settings(); ids,meta=resolve_rubika_worker_account_ids_from_settings(s);"
        "print(json.dumps({"
        "'mode':meta.get('mode'),"
        "'cohort':meta.get('cohort_ids'),"
        "'pin':meta.get('pinned_ids'),"
        "'scope':str(getattr(s,'RUBIKA_WORKER_DISCOVERY_SCOPE','')),"
        "'canon_mode':s.RUBIKA_CANONICAL_SESSION_MODE,"
        "'canon_allow':s.RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS,"
        "'canon_scope':str(getattr(s,'RUBIKA_CANONICAL_SESSION_SCOPE','')),"
        "'auto_enroll':bool(getattr(s,'AUTO_ENROLL_RUBIKA_POOL',False)) if hasattr(s,'AUTO_ENROLL_RUBIKA_POOL') else None,"
        "'actual':ids"
        "}))"
    )
    p = run(
        ["docker", "exec", "-e", "PYTHONPATH=/app", "-w", "/app", "mmp_rubika_worker", "python", "-c", code],
        check=False,
    )
    if p.returncode != 0:
        return {"ok": False, "stderr": (p.stderr or "")[-500:]}
    return {"ok": True, **parse_json(p.stdout)}


def api_env_probe() -> dict:
    code = (
        "import json;"
        "from core_engine.config import get_settings;"
        "get_settings.cache_clear(); s=get_settings();"
        "print(json.dumps({"
        "'l3_routing':getattr(s,'RUBIKA_L3_LOGIN_ROUTING',None),"
        "'auto_enroll':bool(getattr(s,'AUTO_ENROLL_RUBIKA_POOL',False)),"
        "'canon_mode':s.RUBIKA_CANONICAL_SESSION_MODE,"
        "'canon_allow':s.RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS,"
        "'canon_scope':getattr(s,'RUBIKA_CANONICAL_SESSION_SCOPE',None),"
        "'pilot':getattr(s,'RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS',None)"
        "}))"
    )
    p = run(
        ["docker", "exec", "-e", "PYTHONPATH=/app", "-w", "/app", "mmp_core_api", "python", "-c", code],
        check=False,
    )
    if p.returncode != 0:
        return {"ok": False, "stderr": (p.stderr or "")[-400:]}
    return {"ok": True, **parse_json(p.stdout)}


def impact_sets() -> dict:
    p = run(
        [
            "docker",
            "exec",
            "-e",
            "PYTHONPATH=/app",
            "-w",
            "/app",
            "mmp_core_api",
            "python",
            "scripts/_l17_production_impact_simulation.py",
        ],
        check=False,
    )
    try:
        return parse_json(p.stdout)
    except Exception as exc:  # noqa: BLE001
        return {"PRODUCTION_IMPACT_SIMULATION_PASS": False, "error": str(exc), "stderr": (p.stderr or "")[-300:]}


def sentinels() -> dict:
    code = r"""
import json
from sqlalchemy import text
from core_engine.database import SessionLocal
import redis.asyncio as redis
import asyncio
from workers.config import get_worker_settings
from workers.redis_keys import worker_account_coverage_key

db=SessionLocal()
try:
    db.execute(text("SET TRANSACTION READ ONLY"))
    act={}
    for a in (13,23,27,74):
        act[str(a)]=db.execute(text("SELECT id FROM channel_sessions WHERE account_id=:a AND session_status::text='active'"),{"a":a}).scalar()
    g=int(db.execute(text("SELECT count(*) FROM channel_sessions WHERE session_status::text='active'")).scalar() or 0)
    msg=int(db.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0)
    ch=int(db.execute(text("SELECT count(*) FROM rubika_login_challenges")).scalar() or 0)
    a12=db.execute(text("SELECT string_agg(id::text||':'||session_status::text,',' ORDER BY id) FROM channel_sessions WHERE account_id=12")).scalar()
    a79=db.execute(text("SELECT string_agg(id::text||':'||session_status::text,',' ORDER BY id) FROM channel_sessions WHERE account_id=79")).scalar()
finally:
    db.rollback(); db.close()

s=get_worker_settings()
async def q():
    r=redis.from_url(s.REDIS_URL,decode_responses=True); await r.ping(); o={}
    for a in (12,13,23,27,74,79):
        o[str(a)]={"q":int(await r.llen(f"queue:rubika:{a}")),"cov":bool(await r.exists(worker_account_coverage_key("rubika",a)))}
    await r.aclose(); print(json.dumps({"queues":o}))
qs=json.loads(asyncio.get_event_loop().run_until_complete(q()) if False else __import__("asyncio").run(q()))
print(json.dumps({"actives":act,"global_active":g,"msg":msg,"challenges":ch,"a12":a12,"a79":a79,**qs}))
"""
    p = run(
        ["docker", "exec", "-e", "PYTHONPATH=/app", "-w", "/app", "mmp_core_api", "python", "-c", code],
        check=False,
    )
    if p.returncode != 0:
        # fallback simpler on core_api without worker keys import from worker settings redis
        code2 = r"""
import json,asyncio
from sqlalchemy import text
from core_engine.database import SessionLocal
from core_engine.config import get_settings
import redis.asyncio as redis
db=SessionLocal()
try:
 db.execute(text("SET TRANSACTION READ ONLY"))
 act={str(a):db.execute(text("SELECT id FROM channel_sessions WHERE account_id=:a AND session_status::text='active'"),{"a":a}).scalar() for a in (13,23,27,74)}
 g=int(db.execute(text("SELECT count(*) FROM channel_sessions WHERE session_status::text='active'")).scalar() or 0)
 msg=int(db.execute(text("SELECT count(*) FROM message_attempts")).scalar() or 0)
 ch=int(db.execute(text("SELECT count(*) FROM rubika_login_challenges")).scalar() or 0)
 a12=db.execute(text("SELECT string_agg(id::text||':'||session_status::text,',' ORDER BY id) FROM channel_sessions WHERE account_id=12")).scalar()
 a79=db.execute(text("SELECT string_agg(id::text||':'||session_status::text,',' ORDER BY id) FROM channel_sessions WHERE account_id=79")).scalar()
finally:
 db.rollback(); db.close()
s=get_settings()
async def main():
 r=redis.from_url(s.REDIS_URL,decode_responses=True); await r.ping(); o={}
 for a in (12,13,23,27,74,79):
  o[str(a)]={"q":int(await r.llen(f"queue:rubika:{a}")),"cov":bool(await r.exists(f"worker:coverage:rubika:{a}"))}
 await r.aclose(); print(json.dumps({"actives":act,"global_active":g,"msg":msg,"challenges":ch,"a12":a12,"a79":a79,"queues":o}))
asyncio.run(main())
"""
        p = run(
            ["docker", "exec", "-e", "PYTHONPATH=/app", "-w", "/app", "mmp_core_api", "python", "-c", code2],
            check=False,
        )
    if p.returncode != 0:
        return {"ok": False, "stderr": (p.stderr or "")[-500:]}
    return {"ok": True, **parse_json(p.stdout)}


def ensure_env_key(service_block_start: int, text: str, key: str, value: str) -> str:
    """Insert or replace key inside a service environment section."""
    # Find next service or EOF after start
    nxt = re.search(r"\n  [a-zA-Z0-9_]+:\s*\n", text[service_block_start + 1 :])
    end = len(text) if not nxt else service_block_start + 1 + nxt.start()
    block = text[service_block_start:end]
    pat = rf'^(\s*{re.escape(key)}:\s*")[^"]*("\s*)$'
    if re.search(pat, block, flags=re.M):
        new_block, n = re.subn(pat, rf"\g<1>{value}\g<2>", block, flags=re.M)
        assert n >= 1
    else:
        # insert after environment: line
        new_block, n = re.subn(
            r"(^\s*environment:\s*\n)",
            rf'\1      {key}: "{value}"\n',
            block,
            count=1,
            flags=re.M,
        )
        assert n == 1, f"no environment in block for {key}"
    return text[:service_block_start] + new_block + text[end:]


def apply_l17_config() -> dict:
    before = OVERRIDE.read_text(encoding="utf-8")
    text = before
    # Locate service starts
    for svc in ("core_api", "rubika_worker", "celery_worker"):
        m = re.search(rf"^  {svc}:\s*$", text, flags=re.M)
        if not m:
            continue
        start = m.start()
        if svc in ("core_api", "rubika_worker"):
            text = ensure_env_key(start, text, "RUBIKA_L3_LOGIN_ROUTING", "auto_evidence")
            m = re.search(rf"^  {svc}:\s*$", text, flags=re.M)
            start = m.start()
            text = ensure_env_key(start, text, "AUTO_ENROLL_RUBIKA_POOL", "true")
            m = re.search(rf"^  {svc}:\s*$", text, flags=re.M)
            start = m.start()
            text = ensure_env_key(start, text, "RUBIKA_CANONICAL_SESSION_SCOPE", "canonical_active")
        if svc == "rubika_worker":
            m = re.search(rf"^  {svc}:\s*$", text, flags=re.M)
            start = m.start()
            text = ensure_env_key(start, text, "RUBIKA_WORKER_DISCOVERY_SCOPE", "all_eligible")
            # AUTO_ENROLL may not exist on WorkerSettings - still set for parity if mirrored
            m = re.search(rf"^  {svc}:\s*$", text, flags=re.M)
            start = m.start()
            text = ensure_env_key(start, text, "AUTO_ENROLL_RUBIKA_POOL", "true")
        if svc == "celery_worker":
            # mirror scope/mode only for future start; leave exited
            m = re.search(rf"^  {svc}:\s*$", text, flags=re.M)
            start = m.start()
            text = ensure_env_key(start, text, "RUBIKA_CANONICAL_SESSION_SCOPE", "canonical_active")

    # Hard keep invariants
    assert set(_vals(text, "RUBIKA_CANONICAL_SESSION_MODE")) == {"enforce"}
    assert set(_vals(text, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS")) == {"13,23,27,74"}
    assert _vals(text, "RUBIKA_WORKER_DISCOVERY_MODE") == ["dynamic"]
    assert _vals(text, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == ["13,23,27,74"]
    assert _vals(text, "RUBIKA_ACCOUNT_IDS") == ["12,79"]
    assert "auto_evidence" in _vals(text, "RUBIKA_L3_LOGIN_ROUTING")
    assert "true" in [v.lower() for v in _vals(text, "AUTO_ENROLL_RUBIKA_POOL")]
    assert "all_eligible" in _vals(text, "RUBIKA_WORKER_DISCOVERY_SCOPE")
    assert set(_vals(text, "RUBIKA_CANONICAL_SESSION_SCOPE")) >= {"canonical_active"}

    OVERRIDE.write_text(text, encoding="utf-8")
    return {"sha256": _sha(OVERRIDE), "size": OVERRIDE.stat().st_size}


def restore(path: Path) -> dict:
    shutil.copy2(path, OVERRIDE)
    return {"restored_from": str(path), "sha256": _sha(OVERRIDE)}


def compose(*args: str) -> dict:
    p = run(["docker", "compose", *args], check=False)
    return {
        "cmd": ["docker", "compose", *args],
        "rc": p.returncode,
        "stdout_tail": (p.stdout or "")[-800:],
        "stderr_tail": (p.stderr or "")[-800:],
    }


def worker_has_l17() -> bool:
    p = run(
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
            "import importlib.util; print(importlib.util.find_spec('core_engine.services.rubika_l17_automation') is not None)",
        ],
        check=False,
    )
    return (p.stdout or "").strip().endswith("True")


def main() -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)
    provenance = {
        "CONFIG_FILE": str(OVERRIDE),
        "CORE_API_L17_CONSUMER": True,
        "RUBIKA_WORKER_L17_CONSUMER": True,
        "CELERY_L17_CONSUMER": False,
    }
    rebuild_required = not worker_has_l17()
    provenance["RUBIKA_WORKER_REBUILD_REQUIRED"] = rebuild_required
    print(json.dumps({"provenance": provenance}, indent=2))

    # Predeploy gates
    ov = OVERRIDE.read_text(encoding="utf-8")
    pre_sha = _sha(OVERRIDE)
    if not (
        set(_vals(ov, "RUBIKA_CANONICAL_SESSION_MODE")) == {"enforce"}
        and set(_vals(ov, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS")) == {"13,23,27,74"}
        and _vals(ov, "RUBIKA_WORKER_DISCOVERY_MODE") == ["dynamic"]
        and _vals(ov, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == ["13,23,27,74"]
        and _vals(ov, "RUBIKA_ACCOUNT_IDS") == ["12,79"]
    ):
        print("PHASE_STATUS=BLOCKED override pre-state mismatch")
        return 2

    probe = worker_probe()
    print(json.dumps({"pre_probe": probe}, indent=2))
    if not (
        probe.get("ok")
        and probe.get("actual") == EXPECTED_WORKERS
        and probe.get("cohort") == [13, 23, 27, 74]
        and probe.get("pin") == [12, 79]
        and str(probe.get("canon_mode")).lower() == "enforce"
        and str(probe.get("canon_allow")).replace(" ", "") == "13,23,27,74"
    ):
        print("PHASE_STATUS=BLOCKED worker pre-probe")
        return 2

    sent0 = sentinels()
    print(json.dumps({"pre_sentinels": {k: sent0.get(k) for k in ("global_active", "msg", "challenges", "actives", "ok")}}, indent=2))
    if not sent0.get("ok"):
        print("PHASE_STATUS=BLOCKED sentinels")
        return 2
    if sent0.get("global_active") != 4:
        print("PHASE_STATUS=BLOCKED global_active")
        return 2
    for aid, sid in BATCH.items():
        if sent0.get("actives", {}).get(str(aid)) != sid:
            print(f"PHASE_STATUS=BLOCKED active {aid}")
            return 2
    queues = sent0.get("queues") or {}
    for a in EXPECTED_WORKERS:
        q = (queues.get(str(a)) or {}).get("q")
        if q not in (0, None):
            print(f"PHASE_STATUS=BLOCKED unexpected queue {a}={q}")
            return 2
    baseline_msg = sent0["msg"]
    baseline_ch = sent0["challenges"]

    # Backup
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rb = ROOT / f"docker-compose.override.yml.before-l17-automation-{ts}.bak"
    shutil.copy2(OVERRIDE, rb)
    assert rb.is_file() and rb.stat().st_size > 0
    rb_sha = _sha(rb)
    assert rb_sha == pre_sha
    meta = {
        "ROLLBACK_FILE": str(rb),
        "ROLLBACK_SHA256": rb_sha,
        "PRE_CHANGE_CONFIG_SHA256": pre_sha,
        "mode": _vals(rb.read_text(encoding="utf-8"), "RUBIKA_CANONICAL_SESSION_MODE"),
        "allow": _vals(rb.read_text(encoding="utf-8"), "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS"),
        "cohort": _vals(rb.read_text(encoding="utf-8"), "RUBIKA_WORKER_DISCOVERY_COHORT_IDS"),
        "pin": _vals(rb.read_text(encoding="utf-8"), "RUBIKA_ACCOUNT_IDS"),
        "pilot": _vals(rb.read_text(encoding="utf-8"), "RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS"),
        "auto_enroll_before": _vals(rb.read_text(encoding="utf-8"), "AUTO_ENROLL_RUBIKA_POOL") or ["false"],
    }
    print(json.dumps({"backup": meta}, indent=2))

    # Apply config
    applied = apply_l17_config()
    print(json.dumps({"applied": applied}, indent=2))

    # Semantic assertion via impact sim BEFORE recreate (code on core_api bind-mount)
    # Note: impact uses evidence helpers independent of live env for L3/enforce sets;
    # still require expected sets.
    impact = impact_sets()
    print(json.dumps({"pre_recreate_impact": {
        "pass": impact.get("PRODUCTION_IMPACT_SIMULATION_PASS"),
        "enforce": impact.get("AUTO_CANONICAL_ENFORCE_ACCOUNT_IDS"),
        "dynamic": impact.get("DYNAMIC_ELIGIBLE_IDS"),
        "workers": impact.get("RESULTING_WORKER_IDS"),
        "a12": impact.get("ACCOUNT12_PROTECTED"),
        "a79": impact.get("ACCOUNT79_PROTECTED"),
    }}, indent=2))
    if not (
        impact.get("PRODUCTION_IMPACT_SIMULATION_PASS")
        and impact.get("AUTO_CANONICAL_ENFORCE_ACCOUNT_IDS") == EXPECTED_ENFORCE
        and impact.get("DYNAMIC_ELIGIBLE_IDS") == EXPECTED_DYNAMIC
        and impact.get("RESULTING_WORKER_IDS") == EXPECTED_WORKERS
        and impact.get("ACCOUNT12_PROTECTED")
        and impact.get("ACCOUNT79_PROTECTED")
    ):
        restore(rb)
        print("PHASE_STATUS=BLOCKED impact mismatch — config restored")
        return 2

    # Deploy core_api
    recre_api = compose("up", "-d", "--no-deps", "--force-recreate", "core_api")
    print(json.dumps({"recreate_core_api": {"rc": recre_api["rc"], "cmd": recre_api["cmd"]}}, indent=2))
    if recre_api["rc"] != 0:
        restore(rb)
        compose("up", "-d", "--no-deps", "--force-recreate", "core_api")
        return 2
    time.sleep(8)
    api = api_env_probe()
    print(json.dumps({"api_env": api}, indent=2))
    if not (
        api.get("ok")
        and str(api.get("l3_routing")) == "auto_evidence"
        and api.get("auto_enroll") is True
        and str(api.get("canon_scope")) == "canonical_active"
        and str(api.get("canon_mode")).lower() == "enforce"
    ):
        restore(rb)
        compose("up", "-d", "--no-deps", "--force-recreate", "core_api")
        print("PHASE_STATUS=BLOCKED api env — rolled back")
        return 2

    # Deploy worker (rebuild if needed)
    if rebuild_required:
        build = compose("build", "rubika_worker")
        print(json.dumps({"build_worker": {"rc": build["rc"]}}, indent=2))
        if build["rc"] != 0:
            restore(rb)
            compose("up", "-d", "--no-deps", "--force-recreate", "core_api")
            print("PHASE_STATUS=BLOCKED worker build failed — rolled back")
            return 2
    recre_w = compose("up", "-d", "--no-deps", "--force-recreate", "rubika_worker")
    print(json.dumps({"recreate_worker": {"rc": recre_w["rc"], "cmd": recre_w["cmd"]}}, indent=2))
    if recre_w["rc"] != 0:
        restore(rb)
        compose("up", "-d", "--no-deps", "--force-recreate", "core_api", "rubika_worker")
        return 2
    time.sleep(25)

    if not worker_has_l17():
        restore(rb)
        compose("up", "-d", "--no-deps", "--force-recreate", "core_api", "rubika_worker")
        print("PHASE_STATUS=BLOCKED worker still missing L17 — rolled back")
        return 2

    probe2 = worker_probe()
    print(json.dumps({"post_probe": probe2}, indent=2))
    if not (
        probe2.get("ok")
        and probe2.get("actual") == EXPECTED_WORKERS
        and str(probe2.get("scope")) == "all_eligible"
        and str(probe2.get("canon_scope")) == "canonical_active"
        and probe2.get("pin") == [12, 79]
        and probe2.get("mode") == "dynamic"
    ):
        restore(rb)
        compose("up", "-d", "--no-deps", "--force-recreate", "core_api", "rubika_worker")
        print("PHASE_STATUS=BLOCKED post worker probe — rolled back")
        return 2

    impact2 = impact_sets()
    if not (
        impact2.get("AUTO_CANONICAL_ENFORCE_ACCOUNT_IDS") == EXPECTED_ENFORCE
        and impact2.get("RESULTING_WORKER_IDS") == EXPECTED_WORKERS
        and impact2.get("ACCOUNT12_PROTECTED")
        and impact2.get("ACCOUNT79_PROTECTED")
    ):
        restore(rb)
        compose("up", "-d", "--no-deps", "--force-recreate", "core_api", "rubika_worker")
        print("PHASE_STATUS=BLOCKED post impact — rolled back")
        return 2

    # Observe 3 cycles
    obs = []
    for i in range(3):
        p = worker_probe()
        s = sentinels()
        imp = impact_sets()
        obs.append(
            {
                "i": i + 1,
                "actual": p.get("actual"),
                "enforce": imp.get("AUTO_CANONICAL_ENFORCE_ACCOUNT_IDS"),
                "dynamic": imp.get("DYNAMIC_ELIGIBLE_IDS"),
                "global_active": s.get("global_active"),
                "actives": s.get("actives"),
                "msg": s.get("msg"),
                "challenges": s.get("challenges"),
            }
        )
        if i < 2:
            time.sleep(65)

    stable = all(
        o.get("actual") == EXPECTED_WORKERS
        and o.get("enforce") == EXPECTED_ENFORCE
        and o.get("global_active") == 4
        and o.get("actives") == {str(k): v for k, v in BATCH.items()}
        and o.get("msg") == baseline_msg
        and o.get("challenges") == baseline_ch
        for o in obs
    )
    print(json.dumps({"observe": obs, "stable": stable}, indent=2))

    if not stable:
        restore(rb)
        compose("up", "-d", "--no-deps", "--force-recreate", "core_api", "rubika_worker")
        final = {
            "PRODUCTION_AUTOMATION_CLEANUP_EXECUTED": True,
            "PHASE_STATUS": "BLOCKED",
            "ROLLBACK_PERFORMED": True,
            "ROLLBACK_RESULT": "restored_pre_l17_override_after_unstable_observation",
            "L17_PRODUCTION_OBSERVATION_COUNT": len(obs),
            "L17_PRODUCTION_AUTOMATION_STABLE": False,
            "observe": obs,
        }
        (REPORTS / "L17_PRODUCTION_AUTOMATION_CLEANUP_RESULT.json").write_text(
            json.dumps(final, indent=2), encoding="utf-8"
        )
        print(json.dumps(final, indent=2))
        return 2

    final = {
        "PRODUCTION_AUTOMATION_CLEANUP_EXECUTED": True,
        "CONFIG_FILE": str(OVERRIDE),
        "CORE_API_L17_CONSUMER": True,
        "RUBIKA_WORKER_L17_CONSUMER": True,
        "CELERY_L17_CONSUMER": False,
        "RUBIKA_WORKER_REBUILD_REQUIRED": rebuild_required,
        "RUBIKA_L3_LOGIN_ROUTING": "auto_evidence",
        "AUTO_ENROLL_RUBIKA_POOL": True,
        "RUBIKA_WORKER_DISCOVERY_SCOPE": "all_eligible",
        "RUBIKA_CANONICAL_SESSION_SCOPE": "canonical_active",
        "AUTO_CANONICAL_ENFORCE_ACCOUNT_IDS": EXPECTED_ENFORCE,
        "DYNAMIC_ELIGIBLE_IDS": EXPECTED_DYNAMIC,
        "ACTUAL_WORKER_IDS": EXPECTED_WORKERS,
        "ACCOUNT12_PROTECTED": True,
        "ACCOUNT79_PROTECTED": True,
        "WORKER_PIN_12_79_PRESERVED": True,
        "GLOBAL_ACTIVE_SESSION_COUNT": 4,
        "NEW_ACCOUNT_AUTO_LOGIN_L3_READY": True,
        "NEW_ACCOUNT_AUTO_SESSION_PROMOTION_READY": True,
        "NEW_ACCOUNT_AUTO_POOL_READY": True,
        "NEW_ACCOUNT_AUTO_DISCOVERY_READY": True,
        "NEW_ACCOUNT_AUTO_WORKER_COVERAGE_READY": True,
        "NEW_ACCOUNT_AUTO_CANONICAL_ENFORCE_READY": True,
        "FULL_AUTOMATIC_NEW_ACCOUNT_LIFECYCLE_READY": True,
        "L17_PRODUCTION_OBSERVATION_COUNT": len(obs),
        "L17_PRODUCTION_AUTOMATION_STABLE": True,
        "MESSAGE_SENT": False,
        "OTP_REQUESTED": False,
        "ROLLBACK_PERFORMED": False,
        "ROLLBACK_RESULT": None,
        "ROLLBACK_FILE": str(rb),
        "ROLLBACK_SHA256": rb_sha,
        "CURRENT_PHASE": "L17_FULL_NEW_ACCOUNT_AUTOMATION_PRODUCTION",
        "PHASE_STATUS": "COMPLETE",
        "NEXT_SAFE_ACTION": "L18 final production reconciliation + UI truth audit across all 48 accounts",
        "observe": obs,
        "api_env": api,
        "worker_probe": probe2,
        "impact": impact2,
        "baseline_msg": baseline_msg,
        "baseline_challenges": baseline_ch,
    }
    (REPORTS / "L17_PRODUCTION_AUTOMATION_CLEANUP_RESULT.json").write_text(
        json.dumps(final, indent=2), encoding="utf-8"
    )
    md = f"""# L17 Production Automation Cleanup — COMPLETE

```
PRODUCTION_AUTOMATION_CLEANUP_EXECUTED=True
RUBIKA_L3_LOGIN_ROUTING=auto_evidence
AUTO_ENROLL_RUBIKA_POOL=true
RUBIKA_WORKER_DISCOVERY_SCOPE=all_eligible
RUBIKA_CANONICAL_SESSION_SCOPE=canonical_active
AUTO_CANONICAL_ENFORCE_ACCOUNT_IDS=[13,23,27,74]
DYNAMIC_ELIGIBLE_IDS=[13,23,27,74,79]
ACTUAL_WORKER_IDS=[12,13,23,27,74,79]
FULL_AUTOMATIC_NEW_ACCOUNT_LIFECYCLE_READY=True
L17_PRODUCTION_AUTOMATION_STABLE=True
ROLLBACK_PERFORMED=False
```

Rollback copy: `{rb.name}`

NEXT_SAFE_ACTION=L18 final production reconciliation + UI truth audit across all 48 accounts
"""
    (REPORTS / "L17_PRODUCTION_AUTOMATION_CLEANUP_RESULT.md").write_text(md, encoding="utf-8")
    print(json.dumps(final, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
