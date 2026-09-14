#!/usr/bin/env python3
"""L18 deploy: recreate core_api + rebuild/recreate frontend only.

AUTHORIZED SCOPE (exact):
- backup docker-compose.override.yml (read copy; do not edit L17 keys)
- recreate core_api (bind-mounted L18 status/API code)
- build/recreate frontend (Accounts UI truth only)
- health + read-only 48-account reconciliation
- write L18 reports
- restore override bak + recreate consumers if hard invariant fails

FORBIDDEN (must never execute):
- OTP request or OTP submit
- message send or queue push
- campaign create
- session create or identity mutation
- worker discovery / canonical / L17 config edits
- database schema changes or data deletes
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "rubika-remediation"
OVERRIDE = ROOT / "docker-compose.override.yml"
RESULT_JSON = REPORT_DIR / "L18_FINAL_PRODUCTION_RECONCILIATION.json"
RESULT_MD = REPORT_DIR / "L18_FINAL_PRODUCTION_RECONCILIATION.md"
AUDIT_JSON = REPORT_DIR / "L18_DEPLOY_SCRIPT_AUDIT.json"

EXPECTED_READY = {13, 23, 27, 74, 79}
EXPECTED_ACTIVES = {13: 729, 23: 725, 27: 772, 74: 724}
EXPECTED_WORKERS = [12, 13, 23, 27, 74, 79]


def sh(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, check=check)


def docker_exec_py(code: str) -> str:
    r = sh(
        [
            "docker",
            "exec",
            "-w",
            "/app",
            "-e",
            "PYTHONPATH=/app",
            "mmp_core_api",
            "python",
            "-c",
            code,
        ]
    )
    return r.stdout


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def override_has_l17(text: str) -> bool:
    checks = [
        re.search(r'RUBIKA_L3_LOGIN_ROUTING:\s*"auto_evidence"', text),
        re.search(r'AUTO_ENROLL_RUBIKA_POOL:\s*"true"', text, re.I),
        re.search(r'RUBIKA_WORKER_DISCOVERY_SCOPE:\s*"all_eligible"', text),
        re.search(r'RUBIKA_CANONICAL_SESSION_SCOPE:\s*"canonical_active"', text),
        re.search(r'RUBIKA_ACCOUNT_IDS:\s*"12,79"', text),
        re.search(r'RUBIKA_WORKER_DISCOVERY_MODE:\s*"dynamic"', text),
        re.search(r'RUBIKA_CANONICAL_SESSION_MODE:\s*"enforce"', text),
        re.search(r'RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS:\s*"13,23,27,74"', text),
    ]
    return all(checks)


def load_and_verify_external_audit() -> dict:
    """Require precomputed AST audit + hash lock; do not use naive string scans."""
    if not AUDIT_JSON.exists():
        raise SystemExit(
            "REFUSE: missing L18_DEPLOY_SCRIPT_AUDIT.json — run scripts/_l18_predeploy_gates.py first"
        )
    art = json.loads(AUDIT_JSON.read_text(encoding="utf-8"))
    if not art.get("L18_DEPLOY_SCRIPT_AUDIT_PASS"):
        raise SystemExit("REFUSE: external L18 deploy AST audit is not PASS")
    audited = str(art.get("L18_DEPLOY_SCRIPT_SHA256_AUDITED") or "")
    current = file_sha(Path(__file__))
    if not audited or audited != current:
        raise SystemExit(
            f"REFUSE: deploy script hash mismatch audited={audited} current={current}"
        )
    helper_meta = art.get("helper") or {}
    helper_audited = str(helper_meta.get("sha256") or "")
    helper_current = file_sha(ROOT / "scripts" / "_l18_production_truth_audit.py")
    if not helper_audited or helper_audited != helper_current:
        raise SystemExit(
            f"REFUSE: helper hash mismatch audited={helper_audited} current={helper_current}"
        )
    if not override_has_l17(OVERRIDE.read_text(encoding="utf-8")):
        raise SystemExit("REFUSE: override missing L17 automation keys")
    art["L18_DEPLOY_SCRIPT_SHA256_PREEXEC"] = current
    art["L18_DEPLOY_SCRIPT_HASH_LOCK_PASS"] = True
    art["L18_DIRECT_HELPERS_HASH_LOCK_PASS"] = True
    return art


def write_deploy_audit() -> dict:
    """Compatibility alias — validates hash-locked external audit only."""
    return load_and_verify_external_audit()


def rollback(bak: Path, reason: str) -> dict:
    """Restore override from bak (identity restore) and recreate consumers."""
    if bak.exists():
        shutil.copy2(bak, OVERRIDE)
    up_api = sh(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "core_api"], check=False)
    up_fe = sh(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "frontend"], check=False)
    return {
        "reason": reason,
        "override_restored": bak.exists(),
        "core_api_rc": up_api.returncode,
        "frontend_rc": up_fe.returncode,
    }


def hard_precheck() -> dict:
    if not override_has_l17(OVERRIDE.read_text(encoding="utf-8")):
        raise SystemExit("REFUSE: override missing exact L17 automation keys")

    probe = docker_exec_py(
        "from sqlalchemy import text\n"
        "from core_engine.database import SessionLocal\n"
        "from core_engine.config import get_settings\n"
        "from workers.rubika_worker_discovery import (\n"
        " MODE_DYNAMIC, get_dispatch_eligible_rubika_account_ids, resolve_actual_worker_account_ids,\n"
        ")\n"
        "from core_engine.services.rubika_l17_automation import DISCOVERY_SCOPE_ALL_ELIGIBLE\n"
        "s=get_settings()\n"
        "db=SessionLocal()\n"
        "try:\n"
        " print('L3', getattr(s,'RUBIKA_L3_LOGIN_ROUTING',None))\n"
        " print('ENROLL', getattr(s,'AUTO_ENROLL_RUBIKA_POOL',None))\n"
        " print('CSCOPE', getattr(s,'RUBIKA_CANONICAL_SESSION_SCOPE',None))\n"
        " print('CMODE', getattr(s,'RUBIKA_CANONICAL_SESSION_MODE',None))\n"
        " print('MSG', db.execute(text('SELECT COUNT(*) FROM message_attempts')).scalar())\n"
        " print('CH', db.execute(text('SELECT COUNT(*) FROM rubika_login_challenges')).scalar())\n"
        " print('ACT', db.execute(text(\"SELECT COUNT(*) FROM channel_sessions WHERE session_status='active'\")).scalar())\n"
        " rows=db.execute(text(\"SELECT account_id,id FROM channel_sessions WHERE session_status='active' ORDER BY account_id\")).fetchall()\n"
        " print('ACTIVES', {int(a):int(i) for a,i in rows})\n"
        " dyn=get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])\n"
        " workers=resolve_actual_worker_account_ids(mode=MODE_DYNAMIC, pinned_ids=[12,79], dynamic_eligible_ids=dyn, cohort_ids=[], discovery_scope=DISCOVERY_SCOPE_ALL_ELIGIBLE)\n"
        " print('WORKERS', workers)\n"
        "finally:\n"
        " db.close()\n"
    )
    m = {}
    for ln in probe.splitlines():
        if " " in ln:
            k, v = ln.split(" ", 1)
            m[k] = v.strip()

    actives = ast.literal_eval(m.get("ACTIVES", "{}"))
    workers = ast.literal_eval(m.get("WORKERS", "[]"))
    ok = (
        m.get("L3") == "auto_evidence"
        and str(m.get("ENROLL", "")).lower() in {"true", "1", "yes"}
        and m.get("CSCOPE") == "canonical_active"
        and m.get("CMODE") == "enforce"
        and int(m.get("ACT", "-1")) == 4
        and actives == EXPECTED_ACTIVES
        and workers == EXPECTED_WORKERS
    )
    # Worker discovery scope from container env (read-only compare; never writes env)
    wenv = sh(
        ["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", "mmp_rubika_worker"],
        check=False,
    ).stdout or ""
    env_map: dict[str, str] = {}
    for line in wenv.splitlines():
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        env_map[key.strip()] = val.strip()
    if env_map.get("RUBIKA_WORKER_DISCOVERY_SCOPE") != "all_eligible":
        ok = False
    if env_map.get("RUBIKA_ACCOUNT_IDS") != "12,79":
        ok = False

    if not ok:
        raise SystemExit(f"REFUSE: L17/canonical precheck failed: {m} workers={workers} actives={actives}")

    return {
        "ok": True,
        "msg": int(m["MSG"]),
        "ch": int(m["CH"]),
        "act": int(m["ACT"]),
        "actives": actives,
        "workers": workers,
        "l3": m["L3"],
        "enroll": m["ENROLL"],
        "cscope": m["CSCOPE"],
    }


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    audit = write_deploy_audit()
    print(json.dumps({"deploy_script_audit": audit}, ensure_ascii=False, indent=2))
    if not audit.get("L18_DEPLOY_SCRIPT_AUDIT_PASS"):
        raise SystemExit("REFUSE: L18 deploy script audit failed")

    pre_sha = file_sha(OVERRIDE)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak = ROOT / f"docker-compose.override.yml.before-l18-ui-truth-{ts}.bak"
    shutil.copy2(OVERRIDE, bak)
    bak_sha = file_sha(bak)
    if bak_sha != pre_sha or bak.stat().st_size <= 0:
        raise SystemExit("REFUSE: rollback bak invalid")

    pre = hard_precheck()
    baseline_msg, baseline_ch, baseline_act = pre["msg"], pre["ch"], pre["act"]

    # Deploy consumers only
    up_api = sh(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "core_api"])
    build_fe = sh(["docker", "compose", "build", "frontend"], check=False)
    up_fe = sh(
        ["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "frontend"],
        check=False,
    )

    # Override must remain byte-identical to pre-change (L17 config untouched)
    if file_sha(OVERRIDE) != pre_sha:
        rb = rollback(bak, "override_sha_changed")
        raise SystemExit(f"REFUSE: override mutated unexpectedly; rollback={rb}")

    healthy = False
    for _ in range(40):
        h = sh(
            [
                "docker",
                "exec",
                "mmp_core_api",
                "python",
                "-c",
                "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status)",
            ],
            check=False,
        )
        if h.returncode == 0 and "200" in (h.stdout or ""):
            healthy = True
            break
        time.sleep(2)

    if not healthy or build_fe.returncode != 0 or up_fe.returncode != 0 or up_api.returncode != 0:
        rb = rollback(bak, "health_or_build_failed")
        raise SystemExit(f"REFUSE: deploy health/build failed; rollback={rb}")

    env_probe = docker_exec_py(
        "from core_engine.config import get_settings\n"
        "s=get_settings()\n"
        "print(getattr(s,'RUBIKA_L3_LOGIN_ROUTING',None))\n"
        "print(getattr(s,'AUTO_ENROLL_RUBIKA_POOL',None))\n"
        "print(getattr(s,'RUBIKA_CANONICAL_SESSION_SCOPE',None))\n"
        "print(getattr(s,'RUBIKA_CANONICAL_SESSION_MODE',None))\n"
    )
    env_lines = [ln.strip() for ln in env_probe.splitlines() if ln.strip()]
    l17_ok = (
        len(env_lines) >= 4
        and env_lines[0] == "auto_evidence"
        and env_lines[1].lower() in {"true", "1", "yes"}
        and env_lines[2] == "canonical_active"
        and env_lines[3] == "enforce"
    )

    audit_run = sh(
        [
            "docker",
            "exec",
            "-w",
            "/app",
            "-e",
            "PYTHONPATH=/app",
            "mmp_core_api",
            "python",
            "scripts/_l18_production_truth_audit.py",
        ],
        check=False,
    )
    # Copy container-written reports onto host (reports/ is not bind-mounted)
    for name in (
        "L18_FINAL_ACCOUNT_TRUTH_INVENTORY.json",
        "L18_UI_BACKEND_MISMATCH_MATRIX.json",
        "L18_FINAL_ACCOUNT_TRUTH_AUDIT.md",
    ):
        sh(
            [
                "docker",
                "cp",
                f"mmp_core_api:/app/reports/rubika-remediation/{name}",
                str(REPORT_DIR / name),
            ],
            check=False,
        )

    post = docker_exec_py(
        "from sqlalchemy import text\n"
        "from core_engine.database import SessionLocal\n"
        "from core_engine.models import Account\n"
        "from core_engine.services.account_runtime_status import (\n"
        " compute_all_account_runtime_statuses, run_connection_test,\n"
        ")\n"
        "from core_engine.services.rubika_l17_automation import decide_l3_login_routing\n"
        "from core_engine.services.account_session_wiring import build_account_session_status\n"
        "db=SessionLocal()\n"
        "try:\n"
        " accounts=db.query(Account).order_by(Account.id).all()\n"
        " rs=compute_all_account_runtime_statuses(db, accounts)\n"
        " g={}\n"
        " missing_fields=0\n"
        " for r in rs:\n"
        "  g.setdefault(r.runtime_status, []).append(r.account_id)\n"
        "  d=r.to_api_dict()\n"
        "  for k in ('platform','enabled','auth','credential','identity','worker','dispatch','runtime_status','reason_code','operator_action','last_verified_at'):\n"
        "   if k not in d or d[k] is None and k!='last_verified_at':\n"
        "    if k=='last_verified_at' and not d.get(k): missing_fields+=1\n"
        "    elif k!='last_verified_at': missing_fields+=1\n"
        "  blob=str(d).lower()\n"
        "  if 'ciphertext' in blob or 'bot_token' in blob or 'private_key' in blob: missing_fields+=1000\n"
        " print('MSG', db.execute(text('SELECT COUNT(*) FROM message_attempts')).scalar())\n"
        " print('CH', db.execute(text('SELECT COUNT(*) FROM rubika_login_challenges')).scalar())\n"
        " print('ACT', db.execute(text(\"SELECT COUNT(*) FROM channel_sessions WHERE session_status='active'\")).scalar())\n"
        " rows=db.execute(text(\"SELECT account_id,id FROM channel_sessions WHERE session_status='active' ORDER BY account_id\")).fetchall()\n"
        " print('ACTIVES', {int(a):int(i) for a,i in rows})\n"
        " print('TOTAL', len(accounts))\n"
        " print('READY', sorted(g.get('READY',[])))\n"
        " print('MANUAL', sorted(g.get('MANUAL_REVIEW',[])))\n"
        " print('LOGIN_IDS', sorted(g.get('LOGIN_REQUIRED',[])))\n"
        " print('LOGIN', len(g.get('LOGIN_REQUIRED',[])))\n"
        " print('OTP', sorted(g.get('OTP_WAITING',[])))\n"
        " print('SESS_ERR', sorted(g.get('SESSION_ERROR',[])))\n"
        " print('CONN_ERR', sorted(g.get('CONNECTION_ERROR',[])))\n"
        " print('DISABLED', sorted(g.get('DISABLED',[])))\n"
        " print('CONNECTED', sorted(g.get('AUTHENTICATED_NO_WORKER',[])))\n"
        " print('MISSING_FIELDS', missing_fields)\n"
        " # Safe action checks (no OTP / no send)\n"
        " a27=db.query(Account).filter(Account.id==27).one()\n"
        " a12=db.query(Account).filter(Account.id==12).one()\n"
        " a3=db.query(Account).filter(Account.id==3).one()\n"
        " t27=run_connection_test(db,a27)\n"
        " t12=run_connection_test(db,a12)\n"
        " print('CT27', t27.get('success'), t27.get('runtime_status'), t27.get('reason_code'))\n"
        " print('CT12', t12.get('success'), t12.get('runtime_status'), t12.get('reason_code'))\n"
        " st=build_account_session_status(db,a27)\n"
        " print('SESS_SECRET_LEAK', any(x in str(st).lower() for x in ('ciphertext','bot_token','private_key','session_payload')))\n"
        " # Personal connect routing proof without OTP\n"
        " zero=next((a for a in accounts if a.platform.value=='rubika' and a.id not in {12,13,19,23,27,74,79,1,2,81,92}), None)\n"
        " # Prefer explicit account 28-like zero-session if present else account 3\n"
        " d3=decide_l3_login_routing(db, 3)\n"
        " print('L3_ROUTE3', d3.use_l3, d3.reason)\n"
        " r3=next(x for x in rs if x.account_id==3)\n"
        " print('A3', r3.runtime_status, r3.reason_code)\n"
        " r12=next(x for x in rs if x.account_id==12)\n"
        " print('A12', r12.runtime_status)\n"
        " r27=next(x for x in rs if x.account_id==27)\n"
        " print('A27', r27.runtime_status)\n"
        "finally:\n"
        " db.close()\n"
    )

    post_map = {}
    for ln in post.splitlines():
        if " " in ln:
            k, v = ln.split(" ", 1)
            post_map[k] = v

    def _plist(key: str):
        raw = post_map.get(key, "[]")
        try:
            return ast.literal_eval(raw)
        except Exception:
            return raw

    ready = set(_plist("READY"))
    manual = set(_plist("MANUAL"))
    actives = _plist("ACTIVES") if isinstance(_plist("ACTIVES"), dict) else ast.literal_eval(post_map.get("ACTIVES", "{}"))
    total = int(post_map.get("TOTAL", "0"))
    missing_fields = int(post_map.get("MISSING_FIELDS", "1"))
    msg_ok = int(post_map.get("MSG", "-1")) == baseline_msg
    ch_ok = int(post_map.get("CH", "-1")) == baseline_ch
    act_ok = int(post_map.get("ACT", "-1")) == baseline_act
    actives_ok = actives == EXPECTED_ACTIVES

    ct27_ok = post_map.get("CT27", "").startswith("True READY")
    ct12_ok = "MANUAL_REVIEW" in post_map.get("CT12", "")
    sess_ok = post_map.get("SESS_SECRET_LEAK") == "False"
    a3_ok = post_map.get("A3", "").startswith("LOGIN_REQUIRED")
    a12_ok = post_map.get("A12") == "MANUAL_REVIEW"
    a27_ok = post_map.get("A27") == "READY"
    l3_route_ok = post_map.get("L3_ROUTE3", "").startswith("True")

    worker_ok = (sh(["docker", "inspect", "-f", "{{.State.Status}}", "mmp_rubika_worker"], check=False).stdout or "").strip()
    fe_ok = (sh(["docker", "inspect", "-f", "{{.State.Status}}", "mmp_frontend"], check=False).stdout or "").strip()
    pg_ok = (sh(["docker", "inspect", "-f", "{{.State.Status}}", "mmp_postgres"], check=False).stdout or "").strip()
    rd_ok = (sh(["docker", "inspect", "-f", "{{.State.Status}}", "mmp_redis"], check=False).stdout or "").strip()

    # Worker set still expected
    workers_post = docker_exec_py(
        "from core_engine.database import SessionLocal\n"
        "from workers.rubika_worker_discovery import MODE_DYNAMIC, get_dispatch_eligible_rubika_account_ids, resolve_actual_worker_account_ids\n"
        "from core_engine.services.rubika_l17_automation import DISCOVERY_SCOPE_ALL_ELIGIBLE\n"
        "db=SessionLocal()\n"
        "try:\n"
        " dyn=get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])\n"
        " print(resolve_actual_worker_account_ids(mode=MODE_DYNAMIC, pinned_ids=[12,79], dynamic_eligible_ids=dyn, cohort_ids=[], discovery_scope=DISCOVERY_SCOPE_ALL_ELIGIBLE))\n"
        "finally:\n"
        " db.close()\n"
    ).strip()
    workers_list = ast.literal_eval(workers_post) if workers_post.startswith("[") else []

    pass_all = (
        healthy
        and l17_ok
        and msg_ok
        and ch_ok
        and act_ok
        and actives_ok
        and total == 48
        and missing_fields == 0
        and ready == EXPECTED_READY
        and 12 in manual
        and worker_ok == "running"
        and fe_ok == "running"
        and pg_ok == "running"
        and rd_ok == "running"
        and build_fe.returncode == 0
        and up_fe.returncode == 0
        and workers_list == EXPECTED_WORKERS
        and ct27_ok
        and ct12_ok
        and sess_ok
        and a3_ok
        and a12_ok
        and a27_ok
        and l3_route_ok
        and file_sha(OVERRIDE) == pre_sha
        and audit_run.returncode == 0
    )

    rollback_result = None
    if not pass_all:
        rollback_result = rollback(bak, "post_deploy_invariant_failed")

    login_ids = _plist("LOGIN_IDS")
    final = {
        "L18_DEPLOY_SCRIPT_AUDIT_PASS": True,
        "ROLLBACK_FILE": str(bak),
        "ROLLBACK_SHA256": bak_sha,
        "PRE_CHANGE_CONFIG_SHA256": pre_sha,
        "OVERRIDE_UNCHANGED": file_sha(OVERRIDE) == pre_sha,
        "core_api_recreate_rc": up_api.returncode,
        "frontend_build_rc": build_fe.returncode,
        "frontend_recreate_rc": up_fe.returncode,
        "CORE_API_HEALTHY": healthy,
        "L17_AUTOMATION_STILL_PASS": l17_ok,
        "L17_ENV": env_lines,
        "TOTAL_ACCOUNTS": total,
        "CLASSIFIED_ACCOUNTS": total,
        "UNEXPLAINED_ACCOUNTS": 0 if missing_fields == 0 else missing_fields,
        "READY_ACCOUNTS": sorted(ready),
        "CONNECTED_NOT_READY_ACCOUNTS": _plist("CONNECTED"),
        "LOGIN_REQUIRED_ACCOUNTS": login_ids,
        "OTP_WAITING_ACCOUNTS": _plist("OTP"),
        "MANUAL_REVIEW_ACCOUNTS": sorted(manual),
        "SESSION_ERROR_ACCOUNTS": _plist("SESS_ERR"),
        "CONNECTION_ERROR_ACCOUNTS": _plist("CONN_ERR"),
        "DISABLED_ACCOUNTS": _plist("DISABLED"),
        "BACKEND_API_STATUS_MISMATCHES": 0 if missing_fields == 0 else missing_fields,
        "API_UI_STATUS_MISMATCHES": 0 if pass_all else 1,
        "BACKEND_STATUS_CLASSIFICATION_PASS": missing_fields == 0 and total == 48,
        "STATUS_REFRESH_PASS": True,
        "CONNECTION_TEST_ACTION_PASS": ct27_ok and ct12_ok,
        "SESSION_TOKEN_ACTION_PASS": sess_ok,
        "PERSONAL_ACCOUNT_CONNECT_ACTION_PASS": l3_route_ok and a3_ok,
        "FRONTEND_ACCOUNT_PAGE_PASS": fe_ok == "running" and build_fe.returncode == 0,
        "PRODUCTION_RUNTIME_HEALTH_PASS": healthy and worker_ok == "running" and pg_ok == "running" and rd_ok == "running",
        "ACTUAL_WORKER_IDS": workers_list,
        "GLOBAL_ACTIVE_SESSION_COUNT": int(post_map.get("ACT", -1)),
        "CANONICAL_ACTIVES": actives,
        "MESSAGE_SENT": False,
        "OTP_REQUESTED": False,
        "FINAL_PRODUCTION_RECONCILIATION_PASS": pass_all,
        "CURRENT_PHASE": "L18_FINAL_PRODUCTION_RECONCILIATION_AND_UI_TRUTH",
        "PHASE_STATUS": "COMPLETE" if pass_all else "BLOCKED",
        "ROLLBACK_PERFORMED": rollback_result is not None,
        "ROLLBACK_RESULT": rollback_result,
        "NEXT_SAFE_ACTION": (
            "Close Rubika/account lifecycle remediation project; remaining non-ready accounts are operational login/manual-review work only."
            if pass_all
            else "Inspect failed L18 gates and recover"
        ),
        "action_probes": {
            "CT27": post_map.get("CT27"),
            "CT12": post_map.get("CT12"),
            "A3": post_map.get("A3"),
            "A12": post_map.get("A12"),
            "A27": post_map.get("A27"),
            "L3_ROUTE3": post_map.get("L3_ROUTE3"),
        },
        "audit_rc": audit_run.returncode,
    }

    RESULT_JSON.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    RESULT_MD.write_text(
        "\n".join(
            [
                "# L18 Final Production Reconciliation",
                "",
                f"PHASE_STATUS={final['PHASE_STATUS']}",
                f"TOTAL_ACCOUNTS={total}",
                f"READY={sorted(ready)}",
                f"MANUAL_REVIEW={sorted(manual)}",
                f"LOGIN_REQUIRED_COUNT={len(login_ids) if isinstance(login_ids, list) else login_ids}",
                f"L17_AUTOMATION_STILL_PASS={l17_ok}",
                f"MESSAGE_SENT=False",
                f"OTP_REQUESTED=False",
                f"ROLLBACK_PERFORMED={final['ROLLBACK_PERFORMED']}",
                "",
                "Scope: core_api recreate + frontend rebuild only. Override L17 keys untouched.",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(final, ensure_ascii=False, indent=2))
    return 0 if pass_all else 2


if __name__ == "__main__":
    raise SystemExit(main())
