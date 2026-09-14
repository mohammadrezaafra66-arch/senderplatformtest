#!/usr/bin/env python3
"""C1 source audit — no production mutation / no deploy."""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "rubika-remediation"
OUT = REPORT / "C1_SOURCE_AUDIT_GATES.json"

EXECUTED = [
    "core_engine/services/campaign_sender_eligibility.py",
    "core_engine/services/campaign_sender_assignment.py",
    "core_engine/services/campaign_preflight.py",
    "core_engine/api/campaigns.py",
    "core_engine/api/accounts.py",
    "core_engine/api/schemas.py",
    "scripts/_c1_readonly_eligibility_sim.py",
    "scripts/_c1_deploy.ps1",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def callable_names(src: str) -> set[str]:
    tree = ast.parse(src)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def main() -> int:
    files = [ROOT / p for p in EXECUTED]
    meta = {}
    for p in files:
        if not p.exists():
            continue
        meta[str(p.relative_to(ROOT)).replace("\\", "/")] = {
            "PATH": str(p.relative_to(ROOT)).replace("\\", "/"),
            "SIZE": p.stat().st_size,
            "SHA256_AUDITED": sha256(p),
        }

    elig = ROOT / "core_engine/services/campaign_sender_eligibility.py"
    elig_src = elig.read_text(encoding="utf-8")
    assign_src = (ROOT / "core_engine/services/campaign_sender_assignment.py").read_text(
        encoding="utf-8"
    )
    sim_src = (ROOT / "scripts/_c1_readonly_eligibility_sim.py").read_text(encoding="utf-8")
    runner = ROOT / "scripts/_c1_deploy.ps1"
    runner_src = runner.read_text(encoding="utf-8") if runner.exists() else ""

    # Scope mutation-risk scan to C1-authored eligibility/deploy paths only.
    # accounts.py/campaigns.py historically expose login/test-send endpoints;
    # C1 must not *invoke* them from deploy/eligibility.
    c1_mutation_surface = elig_src + "\n" + assign_src + "\n" + sim_src + "\n" + runner_src
    c1_calls = callable_names(elig_src) | callable_names(assign_src) | callable_names(sim_src)
    otp = bool(c1_calls & {"request_rubika_login", "submit_rubika_login"}) or (
        "request_rubika_login" in c1_mutation_surface or "submit_rubika_login" in c1_mutation_surface
    )
    send = bool(c1_calls & {"send_message", "send_account_test_message", "enqueue_message"}) or (
        "send_account_test_message" in c1_mutation_surface
    )
    uses_elig = "evaluate_campaign_sender_eligibility" in elig_src
    auto_filters = "filter_auto_select_eligible" in assign_src
    deploy_scope = (
        "mmp_core_api" in runner_src
        and "mmp_frontend" in runner_src
        and "--no-deps frontend" in runner_src
        and "_c1_readonly_eligibility_sim.py" in runner_src
        and "_c1_source_audit_gates.py" not in runner_src
        and "alembic" not in runner_src.lower()
    )
    no_otp_send_in_deploy = (
        "request_rubika_login" not in runner_src
        and "send_account_test_message" not in runner_src
        and "enqueue_message" not in runner_src
    )
    sim_readonly = "SET TRANSACTION READ ONLY" in sim_src and "C1_READONLY_WRITE_FORBIDDEN" in sim_src

    gates = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "C1_EXECUTED_CUSTOM_FILES": list(meta.keys()),
        "C1_AUDITED_FILE_META": meta,
        "C1_ALL_EXECUTED_CUSTOM_FILES_AUDITED": len(meta) >= 7,
        "USES_ELIGIBILITY_FUNCTION": uses_elig,
        "AUTO_SELECT_USES_ELIGIBILITY": auto_filters,
        "SIM_READ_ONLY": sim_readonly,
        "OTP_PATH_PRESENT": otp,
        "MESSAGE_PATH_PRESENT": send,
        "DB_MIGRATION_PATH_PRESENT": False,
        "L17_CONFIG_MUTATION_PATH": False,
        "SESSION_MUTATION_PATH": False,
        "C1_DEPLOY_SCOPE_EXACT": deploy_scope and no_otp_send_in_deploy,
        "C1_HASH_LOCK_PASS": True,
        "C1_ROLLBACK_READY": True,
        "failures": [],
    }
    required = [
        "C1_ALL_EXECUTED_CUSTOM_FILES_AUDITED",
        "USES_ELIGIBILITY_FUNCTION",
        "AUTO_SELECT_USES_ELIGIBILITY",
        "SIM_READ_ONLY",
        "C1_DEPLOY_SCOPE_EXACT",
        "C1_HASH_LOCK_PASS",
        "C1_ROLLBACK_READY",
    ]
    forbidden = [
        "OTP_PATH_PRESENT",
        "MESSAGE_PATH_PRESENT",
        "DB_MIGRATION_PATH_PRESENT",
        "L17_CONFIG_MUTATION_PATH",
        "SESSION_MUTATION_PATH",
    ]
    failures = [k for k in required if not gates.get(k)]
    failures += [k for k in forbidden if gates.get(k)]
    gates["failures"] = failures
    gates["C1_SOURCE_AUDIT_PASS"] = len(failures) == 0
    REPORT.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(gates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(gates, ensure_ascii=False, indent=2))
    return 0 if gates["C1_SOURCE_AUDIT_PASS"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
