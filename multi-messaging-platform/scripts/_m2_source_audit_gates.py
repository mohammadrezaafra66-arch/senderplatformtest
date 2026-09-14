#!/usr/bin/env python3
"""M2 source-audit gates — NO production mutation.

Audits custom M2 execution files. Does NOT run remediations.
"""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "rubika-remediation"
SCRIPT = ROOT / "scripts" / "_m2_account2_remediation.py"
PREMUT = ROOT / "scripts" / "_m2_premutation_gates.py"
RUNNER = ROOT / "scripts" / "_m2_run_account2_remediation.ps1"
HELPER = ROOT / "core_engine" / "services" / "rubika_legacy_promotion.py"
GATES_SCRIPT = ROOT / "scripts" / "_m2_source_audit_gates.py"
AUDIT_OUT = REPORT / "M2_SOURCE_AUDIT_GATES.json"

EXECUTED = [
    "scripts/_m2_account2_remediation.py",
    "scripts/_m2_premutation_gates.py",
    "scripts/_m2_run_account2_remediation.ps1",
    "core_engine/services/rubika_legacy_promotion.py",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def callable_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def audit() -> dict:
    for p in (SCRIPT, PREMUT, RUNNER, HELPER):
        assert p.exists() and p.stat().st_size > 0, f"missing {p}"

    src = SCRIPT.read_text(encoding="utf-8")
    prem_src = PREMUT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    prem_tree = ast.parse(prem_src)
    calls = callable_names(tree)
    prem_calls = callable_names(prem_tree)
    runner_src = RUNNER.read_text(encoding="utf-8")
    helper_src = HELPER.read_text(encoding="utf-8")

    uses_promote = "promote_proven_legacy_rubika_session" in src
    uses_classify = "classify_legacy_decrypt_failed_session" in src
    uses_rollback = "rollback_legacy_promotion" in src
    direct_sql_active = (
        "SET session_status" in src
        or "update channel_sessions" in src.lower()
        or "UPDATE channel_sessions" in src.lower()
    )
    otp = bool((calls | prem_calls) & {"request_rubika_login", "submit_rubika_login"})
    send = bool(
        (calls | prem_calls)
        & {"send_message", "send_account_test_message", "enqueue_message"}
    )
    targets_ok = (
        "ACCOUNT_ID = 2" in src
        and "PROVEN_SESSION_ID = 721" in src
        and "INVALID_SESSION_ID = 2" in src
    )
    auth_gate = (
        "M2_ALLOW_ACCOUNT2_REMEDIATION" in src
        and "M2_PRODUCTION_TARGET" in src
        and "account2" in src
    )
    # Runner must NOT invoke source-audit regeneration; only consume existing gate JSON.
    runner_chains_audit = bool(
        ("python" in runner_src.lower() or "docker exec" in runner_src.lower())
        and "_m2_source_audit_gates.py" in runner_src
    )
    runner_ok = (
        "_m2_account2_remediation.py" in runner_src
        and "_m2_premutation_gates.py" in runner_src
        and "M2_SOURCE_AUDIT_GATES.json" in runner_src
        and "ErrorActionPreference" in runner_src
        and "compose up" not in runner_src.lower()
        and not runner_chains_audit
        and "M2_PREMUTATION_GATES.json" in runner_src
    )
    helper_has_classify = "def classify_legacy_decrypt_failed_session" in helper_src
    prem_readonly = (
        "SET TRANSACTION READ ONLY" in prem_src
        and "M2_PREMUTATION_WRITE_FORBIDDEN" in prem_src
        and "promote_proven_legacy_rubika_session" not in prem_src
    )
    unrelated_mut = bool(
        calls & {"promote_proven_legacy_rubika_session"}
    ) and ("account_id=12" in src.replace(" ", "") or "ACCOUNT_ID = 12" in src)

    script_sha = sha256(SCRIPT)
    prem_sha = sha256(PREMUT)
    runner_sha = sha256(RUNNER)
    helper_sha = sha256(HELPER)

    gates = {
        "M2_EXECUTED_CUSTOM_FILES": EXECUTED,
        "M2_ALL_EXECUTED_CUSTOM_FILES_AUDITED": True,
        "M2_SCRIPT_EXISTS": True,
        "M2_SCRIPT_SIZE": SCRIPT.stat().st_size,
        "M2_SCRIPT_SHA256_AUDITED": script_sha,
        "M2_PREMUTATION_SHA256_AUDITED": prem_sha,
        "M2_RUNNER_SHA256_AUDITED": runner_sha,
        "M2_HELPER_SHA256_AUDITED": helper_sha,
        "M2_HELPER_HASHES": {
            "scripts/_m2_account2_remediation.py": script_sha,
            "scripts/_m2_premutation_gates.py": prem_sha,
            "scripts/_m2_run_account2_remediation.ps1": runner_sha,
            "core_engine/services/rubika_legacy_promotion.py": helper_sha,
        },
        "M2_AUDITED_FILE_META": {
            "scripts/_m2_account2_remediation.py": {
                "PATH": "scripts/_m2_account2_remediation.py",
                "SIZE": SCRIPT.stat().st_size,
                "SHA256_AUDITED": script_sha,
            },
            "scripts/_m2_premutation_gates.py": {
                "PATH": "scripts/_m2_premutation_gates.py",
                "SIZE": PREMUT.stat().st_size,
                "SHA256_AUDITED": prem_sha,
            },
            "scripts/_m2_run_account2_remediation.ps1": {
                "PATH": "scripts/_m2_run_account2_remediation.ps1",
                "SIZE": RUNNER.stat().st_size,
                "SHA256_AUDITED": runner_sha,
            },
            "core_engine/services/rubika_legacy_promotion.py": {
                "PATH": "core_engine/services/rubika_legacy_promotion.py",
                "SIZE": HELPER.stat().st_size,
                "SHA256_AUDITED": helper_sha,
            },
        },
        "ACCOUNT2_REMEDIATION_HELPER": "promote_proven_legacy_rubika_session",
        "ACCOUNT2_INVALID_SESSION_TREATMENT": "DECRYPT_FAILED",
        "DIRECT_DB_EDIT_REQUIRED": False,
        "USES_PROMOTE_HELPER": uses_promote,
        "USES_CLASSIFY_HELPER": uses_classify and helper_has_classify,
        "USES_ROLLBACK_HELPER": uses_rollback,
        "DIRECT_SQL_ACTIVE_PATH": direct_sql_active,
        "OTP_PATH_PRESENT": otp,
        "MESSAGE_PATH_PRESENT": send,
        "MESSAGE_SEND_PATH_PRESENT": send,
        "DB_MIGRATION_PATH_PRESENT": "alembic" in src.lower() or "alembic" in runner_src.lower(),
        "L17_CONFIG_MUTATION_PATH": "docker-compose.override" in runner_src.lower()
        or "OVERRIDE.write" in src,
        "UNRELATED_ACCOUNT_MUTATION_PATH": unrelated_mut,
        "TARGET_CONSTANTS_OK": targets_ok,
        "AUTH_ENV_GATE_PRESENT": auth_gate,
        "PREMUTATION_READ_ONLY": prem_readonly,
        "RUNNER_DOES_NOT_CHAIN_SOURCE_AUDIT": not runner_chains_audit,
        "M2_MUTATION_SCOPE_EXACT": uses_promote
        and uses_classify
        and targets_ok
        and not direct_sql_active
        and not otp
        and not send
        and prem_readonly,
        "M2_RUNNER_SOURCE_AUDIT_PASS": runner_ok,
        "POOL_CONFIG_MUTATION_PATH": "ensure_rubika_pool_membership" in calls,
        "CONFIG_MUTATION_PATH": "docker-compose.override" in runner_src.lower(),
    }

    failures = []
    for k in (
        "M2_SCRIPT_EXISTS",
        "M2_ALL_EXECUTED_CUSTOM_FILES_AUDITED",
        "USES_PROMOTE_HELPER",
        "USES_CLASSIFY_HELPER",
        "USES_ROLLBACK_HELPER",
        "TARGET_CONSTANTS_OK",
        "AUTH_ENV_GATE_PRESENT",
        "PREMUTATION_READ_ONLY",
        "RUNNER_DOES_NOT_CHAIN_SOURCE_AUDIT",
        "M2_MUTATION_SCOPE_EXACT",
        "M2_RUNNER_SOURCE_AUDIT_PASS",
    ):
        if not gates.get(k):
            failures.append(f"{k}=False")
    for k in (
        "DIRECT_SQL_ACTIVE_PATH",
        "OTP_PATH_PRESENT",
        "MESSAGE_PATH_PRESENT",
        "DB_MIGRATION_PATH_PRESENT",
        "L17_CONFIG_MUTATION_PATH",
        "UNRELATED_ACCOUNT_MUTATION_PATH",
        "POOL_CONFIG_MUTATION_PATH",
        "CONFIG_MUTATION_PATH",
    ):
        if gates.get(k):
            failures.append(f"{k}=True")

    gates["failures"] = failures
    gates["M2_SOURCE_AUDIT_PASS"] = len(failures) == 0
    gates["M2_PREEXEC_ALL_GATES_PASS"] = None
    return gates


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    art = {"generated_at": datetime.now(timezone.utc).isoformat(), **audit()}
    payload = json.dumps(art, ensure_ascii=False, indent=2) + "\n"
    art["M2_SOURCE_AUDIT_GATES_SHA256"] = hashlib.sha256(payload.encode()).hexdigest()
    art["M2_GATE_FILE_CURRENT"] = True
    payload = json.dumps(art, ensure_ascii=False, indent=2) + "\n"
    AUDIT_OUT.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if art["M2_SOURCE_AUDIT_PASS"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
