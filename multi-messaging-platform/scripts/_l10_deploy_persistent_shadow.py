#!/usr/bin/env python3
"""L10 deploy orchestrator: hard-gate -> recreate -> verify -> optional rollback.

Runs on the host. Never prints secrets. Never enables enforce / removes pin.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "rubika-remediation"
META = REPORT_DIR / "_l10_rollback_meta.txt"
CONFIG = ROOT / "docker-compose.override.yml"
RESULT_JSON = REPORT_DIR / "L10_PERSISTENT_BATCH_A_SHADOW_RESULT.json"
HARD_GATE = ROOT / "scripts" / "_l10_hard_gate_config.py"

BATCH_A = (13, 23, 74)
EXPECTED = {13: 729, 23: 725, 74: 724}
PINNED = (12, 79)
BASELINE = {
    "msg_attempts": 5,
    "login_challenges": 0,
    "a12": "657:legacy_unclassified,728:legacy_unclassified",
    "a79": "600:legacy_unclassified",
}


def run(cmd: list[str], check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess:
    print(f"+ {' '.join(cmd)}", flush=True)
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        check=check,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def run_out(cmd: list[str], timeout: int = 120) -> str:
    p = run(cmd, check=True, timeout=timeout)
    return (p.stdout or "") + (p.stderr or "")


def hard_gate() -> dict[str, str]:
    p = subprocess.run(
        [sys.executable, str(HARD_GATE)],
        cwd=str(ROOT),
        check=False,
        text=True,
        capture_output=True,
    )
    out = (p.stdout or "") + (p.stderr or "")
    print(out, flush=True)
    if p.returncode != 0:
        raise SystemExit("HARD_GATE_FAILED")
    props = {}
    for line in out.splitlines():
        if "=" in line and not line.startswith(" ") and not line.startswith("-"):
            k, v = line.split("=", 1)
            if k.isupper() or k.startswith("CONFIG_") or k.startswith("HARD_"):
                props[k] = v
    if props.get("HARD_GATE_PASS") != "True":
        raise SystemExit("HARD_GATE_PASS_NOT_TRUE")
    return props


def docker_exec(container: str, *args: str, check: bool = True, timeout: int = 120) -> str:
    return run_out(["docker", "exec", container, *args], timeout=timeout)


def psql(sql: str) -> str:
    return docker_exec(
        "mmp_postgres",
        "psql",
        "-U",
        "mmp_user",
        "-d",
        "mmp_db",
        "-t",
        "-A",
        "-c",
        sql,
    ).strip()


def snapshot_db() -> dict:
    return {
        "global_active": int(
            psql(
                "SELECT count(*) FROM channel_sessions WHERE session_status::text='active'"
            )
            or "0"
        ),
        "a13": psql(
            "SELECT coalesce(string_agg(id::text||':'||session_status::text, ',' ORDER BY id),'NONE') FROM channel_sessions WHERE account_id=13"
        ),
        "a23": psql(
            "SELECT coalesce(string_agg(id::text||':'||session_status::text, ',' ORDER BY id),'NONE') FROM channel_sessions WHERE account_id=23"
        ),
        "a74": psql(
            "SELECT coalesce(string_agg(id::text||':'||session_status::text, ',' ORDER BY id),'NONE') FROM channel_sessions WHERE account_id=74"
        ),
        "a12": psql(
            "SELECT coalesce(string_agg(id::text||':'||session_status::text, ',' ORDER BY id),'NONE') FROM channel_sessions WHERE account_id=12"
        ),
        "a79": psql(
            "SELECT coalesce(string_agg(id::text||':'||session_status::text, ',' ORDER BY id),'NONE') FROM channel_sessions WHERE account_id=79"
        ),
        "msg_attempts": int(psql("SELECT count(*) FROM message_attempts") or "0"),
        "login_challenges": int(
            psql("SELECT count(*) FROM rubika_login_challenges") or "0"
        ),
    }


def snapshot_queues() -> dict[str, int]:
    code = (
        "from core_engine.config import get_settings\n"
        "import redis\n"
        "c=redis.Redis.from_url(get_settings().REDIS_URL, decode_responses=True, socket_connect_timeout=5)\n"
        "c.ping()\n"
        "for aid in (12,79,13,23,74):\n"
        " print(f'{aid}={c.llen(f\"queue:rubika:{aid}\")}')\n"
        "c.close()\n"
    )
    out = docker_exec("mmp_core_api", "python", "-c", code)
    res = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.strip().split("=", 1)
            if k.isdigit():
                res[k] = int(v)
    return res


def runtime_canonical(container: str) -> tuple[str, str]:
    code = (
        "from workers.config import WorkerSettings\n"
        "s=WorkerSettings()\n"
        "print('MODE='+s.RUBIKA_CANONICAL_SESSION_MODE)\n"
        "print('ALLOW='+(s.RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS or ''))\n"
    )
    out = docker_exec(container, "python", "-c", code)
    mode = allow = ""
    for line in out.splitlines():
        if line.startswith("MODE="):
            mode = line.split("=", 1)[1].strip()
        if line.startswith("ALLOW="):
            allow = line.split("=", 1)[1].strip()
    return mode, allow


def core_api_health() -> dict:
    info = {"running": False, "http": False, "db": False, "redis": False, "mode": "", "allow": ""}
    st = run_out(
        [
            "docker",
            "inspect",
            "-f",
            "{{.State.Running}} {{.State.Status}} {{.RestartCount}}",
            "mmp_core_api",
        ]
    ).strip()
    info["running"] = st.startswith("true")
    try:
        http = run_out(
            [
                "docker",
                "exec",
                "mmp_core_api",
                "python",
                "-c",
                "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=10); print(r.status)",
            ]
        )
        info["http"] = "200" in http
    except Exception as e:
        info["http_error"] = str(e)
    try:
        db = docker_exec(
            "mmp_core_api",
            "python",
            "-c",
            "from core_engine.database import SessionLocal\n"
            "from sqlalchemy import text\n"
            "db=SessionLocal()\n"
            "print(db.execute(text('SELECT 1')).scalar())\n"
            "db.close()\n",
        )
        info["db"] = "1" in db
    except Exception as e:
        info["db_error"] = str(e)
    try:
        rd = docker_exec(
            "mmp_core_api",
            "python",
            "-c",
            "from core_engine.config import get_settings\n"
            "import redis\n"
            "c=redis.Redis.from_url(get_settings().REDIS_URL, socket_connect_timeout=5)\n"
            "print(c.ping())\n"
            "c.close()\n",
        )
        info["redis"] = "True" in rd
    except Exception as e:
        info["redis_error"] = str(e)
    try:
        info["mode"], info["allow"] = runtime_canonical("mmp_core_api")
    except Exception as e:
        info["cfg_error"] = str(e)
    return info


def rollback_config() -> None:
    meta: dict[str, str] = {}
    for line in META.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            meta[k.strip()] = v.strip()
    bak = ROOT / meta["ROLLBACK_COPY"]
    shutil.copy2(bak, CONFIG)
    print(f"ROLLBACK_RESTORED_FROM={bak.name}", flush=True)


def wait_running(name: str, seconds: int = 90) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        st = run(
            ["docker", "inspect", "-f", "{{.State.Running}} {{.State.Status}} {{.RestartCount}}", name],
            check=False,
        )
        out = ((st.stdout or "") + (st.stderr or "")).strip()
        print(f"WAIT {name}: {out}", flush=True)
        if out.startswith("true running") or out.startswith("true "):
            # ensure not crash-looping: restart count shouldn't spike immediately
            parts = out.split()
            if len(parts) >= 3:
                try:
                    if int(parts[2]) > 3:
                        return False
                except ValueError:
                    pass
            return True
        time.sleep(3)
    return False


def shadow_compare() -> dict:
    # Persistent env already loaded in core_api; read-only compare only (no plaintext).
    code = r"""
import json
from core_engine.database import SessionLocal
from core_engine.services.rubika_canonical_runtime import (
    compare_legacy_vs_canonical,
    select_legacy_rubika_session_row,
)

BATCH = [13, 23, 74]
db = SessionLocal()
out = {}
try:
    for aid in BATCH:
        legacy = select_legacy_rubika_session_row(db, aid)
        cmp = compare_legacy_vs_canonical(db, aid, legacy_row=legacy)
        out[str(aid)] = cmp.as_safe_dict()
    print(json.dumps(out))
finally:
    db.close()
"""
    raw = docker_exec("mmp_core_api", "python", "-c", code, timeout=180)
    line = [ln for ln in raw.splitlines() if ln.strip().startswith("{")][-1]
    return json.loads(line)


def main() -> int:
    os.chdir(ROOT)
    result: dict = {
        "phase": "L10_PERSISTENT_BATCH_A_SHADOW_DEPLOYMENT",
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "rollback_performed": False,
        "message_sent": False,
        "otp_requested": False,
        "production_enforce_enabled": False,
        "worker_pin_removed": False,
        "celery_left_exited": True,
        "rubika_worker_rebuilt": False,
    }

    print("=== L10 HARD GATE ===", flush=True)
    props = hard_gate()
    result["config_provenance"] = props
    result["config_rollback_gate_pass"] = props.get("CONFIG_ROLLBACK_GATE_PASS") == "True"
    result["config_exact_value_gate_pass"] = props.get("CONFIG_EXACT_VALUE_GATE_PASS") == "True"

    print("=== PREDEPLOY SNAPSHOT ===", flush=True)
    before = snapshot_db()
    result["before"] = before
    assert before["global_active"] == 3, before
    assert before["a13"] == "729:active", before
    assert before["a23"] == "725:active", before
    assert before["a74"] == "724:active", before
    assert before["a12"] == BASELINE["a12"], before
    assert before["a79"] == BASELINE["a79"], before
    assert before["msg_attempts"] == BASELINE["msg_attempts"], before
    assert before["login_challenges"] == BASELINE["login_challenges"], before

    # --- core_api recreate ---
    print("=== RECREATE core_api ===", flush=True)
    try:
        p = run(
            ["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "core_api"],
            check=True,
            timeout=180,
        )
        print(p.stdout, flush=True)
        print(p.stderr, flush=True)
    except subprocess.CalledProcessError as e:
        print(e.stdout, flush=True)
        print(e.stderr, flush=True)
        raise

    if not wait_running("mmp_core_api"):
        print("CORE_API_NOT_RUNNING -> ROLLBACK", flush=True)
        rollback_config()
        result["rollback_performed"] = True
        run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "core_api"], check=False)
        wait_running("mmp_core_api")
        result["phase_status"] = "BLOCKED"
        result["rollback_result"] = "core_api_not_running"
        RESULT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 2

    time.sleep(5)
    health = core_api_health()
    result["core_api_health"] = health
    ok = (
        health["running"]
        and health["http"]
        and health["db"]
        and health["redis"]
        and health["mode"] == "shadow"
        and health["allow"] == "13,23,74"
    )
    if not ok:
        print(f"CORE_API_HEALTH_FAIL={health} -> ROLLBACK", flush=True)
        rollback_config()
        result["rollback_performed"] = True
        run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "core_api"], check=False)
        wait_running("mmp_core_api")
        recovered = core_api_health()
        result["rollback_result"] = {"reason": "core_api_health_fail", "recovered": recovered}
        result["phase_status"] = "BLOCKED"
        RESULT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 2
    result["core_api_health_pass"] = True
    print("CORE_API_HEALTH_PASS=True", flush=True)

    # --- celery: leave exited (no session_access path; L9 topology) ---
    celery_status = run(
        ["docker", "inspect", "-f", "{{.State.Status}}", "mmp_celery_worker"],
        check=False,
    )
    celery_st = ((celery_status.stdout or "") + (celery_status.stderr or "")).strip()
    result["celery_status"] = celery_st
    result["celery_worker_health_pass"] = celery_st in {"exited", "created"}
    result["celery_action"] = "left_exited_no_session_access_import"
    print(f"CELERY_LEFT_AS={celery_st}", flush=True)

    # --- rubika_worker rebuild + recreate ---
    print("=== BUILD rubika_worker ===", flush=True)
    try:
        bp = run(
            ["docker", "compose", "build", "rubika_worker"],
            check=True,
            timeout=900,
        )
        print(bp.stdout[-4000:] if bp.stdout else "", flush=True)
        print(bp.stderr[-2000:] if bp.stderr else "", flush=True)
        result["rubika_worker_rebuilt"] = True
    except subprocess.CalledProcessError as e:
        print(e.stdout, flush=True)
        print(e.stderr, flush=True)
        print("RUBIKA_BUILD_FAIL -> ROLLBACK", flush=True)
        rollback_config()
        result["rollback_performed"] = True
        run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "core_api"], check=False)
        wait_running("mmp_core_api")
        result["phase_status"] = "BLOCKED"
        result["rollback_result"] = "rubika_worker_build_fail"
        RESULT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 2

    print("=== RECREATE rubika_worker ===", flush=True)
    run(
        ["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "rubika_worker"],
        check=True,
        timeout=180,
    )
    if not wait_running("mmp_rubika_worker", seconds=120):
        print("RUBIKA_WORKER_NOT_RUNNING -> ROLLBACK", flush=True)
        rollback_config()
        result["rollback_performed"] = True
        run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "core_api"], check=False)
        # restore prior worker image behavior by recreate with old config; image stays new but mode off
        run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "rubika_worker"], check=False)
        wait_running("mmp_core_api")
        result["phase_status"] = "BLOCKED"
        result["rollback_result"] = "rubika_worker_not_running"
        RESULT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 2

    time.sleep(5)
    pin = docker_exec("mmp_rubika_worker", "printenv", "RUBIKA_ACCOUNT_IDS").strip()
    wmode, wallow = runtime_canonical("mmp_rubika_worker")
    # also verify L7 module present
    has_l7 = run(
        [
            "docker",
            "exec",
            "mmp_rubika_worker",
            "python",
            "-c",
            "import core_engine.services.rubika_canonical_runtime as m; print('HAS_L7='+str(hasattr(m,'MODE_SHADOW'))); print('SHADOW_MATCH' in open(m.__file__,encoding='utf-8').read())",
        ],
        check=False,
    )
    l7_out = ((has_l7.stdout or "") + (has_l7.stderr or "")).strip()
    print(f"WORKER_PIN={pin}", flush=True)
    print(f"WORKER_MODE={wmode}", flush=True)
    print(f"WORKER_ALLOW={wallow}", flush=True)
    print(f"L7_CHECK={l7_out}", flush=True)
    result["rubika_worker"] = {
        "pin": pin,
        "mode": wmode,
        "allow": wallow,
        "l7": l7_out,
    }
    worker_ok = (
        pin == "12,79"
        and wmode == "shadow"
        and wallow == "13,23,74"
        and "HAS_L7=True" in l7_out
    )
    if not worker_ok:
        print("RUBIKA_WORKER_CONFIG_FAIL -> ROLLBACK", flush=True)
        rollback_config()
        result["rollback_performed"] = True
        run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "core_api"], check=False)
        run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "rubika_worker"], check=False)
        wait_running("mmp_core_api")
        result["phase_status"] = "BLOCKED"
        result["rollback_result"] = {"reason": "rubika_worker_config_fail", "detail": result["rubika_worker"]}
        RESULT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 2
    result["rubika_worker_health_pass"] = True
    result["worker_pin_preserved"] = True
    result["worker_pin_blocks_batch_a_runtime_shadow"] = True

    # --- postdeploy ---
    print("=== POSTDEPLOY ===", flush=True)
    after = snapshot_db()
    queues = snapshot_queues()
    result["after"] = after
    result["queues"] = queues
    post_ok = (
        after["global_active"] == 3
        and after["a13"] == "729:active"
        and after["a23"] == "725:active"
        and after["a74"] == "724:active"
        and after["a12"] == before["a12"]
        and after["a79"] == before["a79"]
        and after["msg_attempts"] == before["msg_attempts"]
        and after["login_challenges"] == before["login_challenges"]
        and all(queues.get(str(a), -1) == 0 for a in (12, 79, 13, 23, 74))
    )
    if not post_ok:
        print(f"POSTDEPLOY_FAIL after={after} queues={queues} -> ROLLBACK", flush=True)
        rollback_config()
        result["rollback_performed"] = True
        run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "core_api"], check=False)
        run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "rubika_worker"], check=False)
        result["phase_status"] = "BLOCKED"
        result["rollback_result"] = "postdeploy_invariant_fail"
        RESULT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 2

    print("=== SHADOW READ CHECK ===", flush=True)
    try:
        shadow = shadow_compare()
    except Exception as e:
        print(f"SHADOW_COMPARE_FAIL={e} -> ROLLBACK", flush=True)
        rollback_config()
        result["rollback_performed"] = True
        run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "core_api"], check=False)
        run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "rubika_worker"], check=False)
        result["phase_status"] = "BLOCKED"
        result["rollback_result"] = f"shadow_compare_exception:{e}"
        RESULT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 2

    result["shadow"] = shadow
    shadow_ok = True
    for aid, sid in EXPECTED.items():
        row = shadow.get(str(aid), {})
        legacy = row.get("legacy_selected_session_id")
        canon = row.get("canonical_selected_session_id")
        metric = row.get("metric") or (
            "SHADOW_MATCH" if row.get("match") else "SHADOW_UNKNOWN"
        )
        if legacy != sid or canon != sid or metric != "SHADOW_MATCH":
            shadow_ok = False
            print(f"SHADOW_MISMATCH account={aid} row={row}", flush=True)
    if not shadow_ok:
        rollback_config()
        result["rollback_performed"] = True
        run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "core_api"], check=False)
        run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "rubika_worker"], check=False)
        result["phase_status"] = "BLOCKED"
        result["rollback_result"] = "shadow_mismatch"
        RESULT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return 2

    # final sentinel (shadow must not mutate)
    final = snapshot_db()
    result["final"] = final
    if final != after:
        # allow equality of key fields
        if (
            final["global_active"] != after["global_active"]
            or final["msg_attempts"] != after["msg_attempts"]
            or final["login_challenges"] != after["login_challenges"]
            or final["a13"] != after["a13"]
            or final["a23"] != after["a23"]
            or final["a74"] != after["a74"]
        ):
            print("MUTATION_AFTER_SHADOW -> ROLLBACK", flush=True)
            rollback_config()
            result["rollback_performed"] = True
            run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "core_api"], check=False)
            run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "rubika_worker"], check=False)
            result["phase_status"] = "BLOCKED"
            result["rollback_result"] = "mutation_after_shadow"
            RESULT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return 2

    result["phase_status"] = "COMPLETE"
    result["persistent_batch_a_shadow_enabled"] = True
    result["persistent_mode"] = "shadow"
    result["persistent_allowlist"] = "13,23,74"
    result["rubika_account_ids"] = "12,79"
    result["account13_shadow_result"] = "SHADOW_MATCH"
    result["account23_shadow_result"] = "SHADOW_MATCH"
    result["account74_shadow_result"] = "SHADOW_MATCH"
    result["account13_active_session"] = 729
    result["account23_active_session"] = 725
    result["account74_active_session"] = 724
    result["global_active_session_count"] = 3
    result["account12_unchanged"] = True
    result["account79_unchanged"] = True
    result["db_unexpected_mutation_detected"] = False
    result["redis_unexpected_mutation_detected"] = False
    RESULT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("PHASE_STATUS=COMPLETE", flush=True)
    print(f"RESULT_JSON={RESULT_JSON}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"UNHANDLED={e}", flush=True)
        raise
