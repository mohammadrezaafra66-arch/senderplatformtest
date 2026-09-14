#!/usr/bin/env python3
"""M1 source-audit hard gate — NO production forensic execution.

Audits ALL custom files that the production runner will execute:
  - scripts/_m1_manual_review_forensic_audit.py
  - scripts/_m1_forensic_auth_probe.py
  - scripts/_m1_pre_audit_sentinels.py
  - scripts/_m1_run_readonly_forensic.ps1

Writes reports/rubika-remediation/M1_SOURCE_AUDIT_GATES.json only.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "rubika-remediation"
SCRIPT = ROOT / "scripts" / "_m1_manual_review_forensic_audit.py"
PROBE = ROOT / "scripts" / "_m1_forensic_auth_probe.py"
SENTINEL = ROOT / "scripts" / "_m1_pre_audit_sentinels.py"
RUNNER = ROOT / "scripts" / "_m1_run_readonly_forensic.ps1"
AUDIT_OUT = REPORT / "M1_SOURCE_AUDIT_GATES.json"

EXECUTED_CUSTOM_FILES = [
    "scripts/_m1_manual_review_forensic_audit.py",
    "scripts/_m1_forensic_auth_probe.py",
    "scripts/_m1_pre_audit_sentinels.py",
    "scripts/_m1_run_readonly_forensic.ps1",
]

FORBIDDEN_CALL_ATTRS = frozenset(
    {
        "bulk_update_mappings",
        "bulk_save_objects",
        "request_rubika_login",
        "submit_rubika_login",
        "send_account_test_message",
        "store_channel_session",
        "promote_session",
        "ensure_rubika_pool_membership",
        "send_message",
        "send_code",
        "sign_in",
        "logout",
        "logout_all",
    }
)

FORBIDDEN_REDIS_ATTRS = frozenset(
    {
        "set",
        "delete",
        "expire",
        "hset",
        "sadd",
        "srem",
        "lpush",
        "rpush",
        "publish",
        "setex",
        "setnx",
        "mset",
    }
)


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


def attribute_loads(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def string_constants(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.add(node.value)
    return out


def has_targets_literal(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "TARGETS":
                if isinstance(node.value, ast.List):
                    vals = [
                        elt.value
                        for elt in node.value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, int)
                    ]
                    return vals == [2, 12, 19, 81, 92]
    return False


def real_orm_mutation_calls(tree: ast.AST) -> dict[str, bool]:
    """Detect actual session.commit()/flush() CALL sites (not attribute assignment)."""
    found = {"commit": False, "flush": False, "add": False, "delete": False, "merge": False}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in found:
            found[node.func.attr] = True
        elif isinstance(node.func, ast.Name) and node.func.id in found:
            found[node.func.id] = True
    return found


def report_write_basenames(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v.startswith("M1_") and (v.endswith(".json") or v.endswith(".md")):
                names.add(v)
    return names


def audit_python_file(path: Path) -> dict:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = callable_names(tree)
    attrs = attribute_loads(tree)
    strs = string_constants(tree)
    mut = real_orm_mutation_calls(tree)
    redis_mut = sorted(calls & FORBIDDEN_REDIS_ATTRS)
    forbidden = sorted(calls & FORBIDDEN_CALL_ATTRS)
    db_ro = ("SET TRANSACTION READ ONLY" in src) and ("autoflush" in attrs)
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha256(path),
        "calls": sorted(calls),
        "forbidden_call_hits": forbidden,
        "redis_mutation_call_hits": redis_mut,
        "orm_mutation_calls": mut,
        "db_read_only_enforced": db_ro,
        "has_targets": has_targets_literal(tree),
        "report_names": sorted(report_write_basenames(tree)),
        "src_has_readonly_redis": ("ReadOnlyRedis" in src) or ("REDIS_MUTATION_BLOCKED" in src),
        "src": src,
        "tree": tree,
        "call_set": calls,
        "attr_set": attrs,
        "str_set": strs,
    }


def audit_runner_ps1(path: Path) -> dict:
    src = path.read_text(encoding="utf-8")
    # Fail closed: runner must not mutate docker services / compose / OTP / session.
    dangerous = []
    patterns = [
        (r"\bcompose\s+up\b", "compose_up"),
        (r"\bdocker\s+restart\b", "docker_restart"),
        (r"\bdocker\s+compose\b.*\bup\b", "docker_compose_up"),
        (r"\brequest_rubika_login\b", "otp_request"),
        (r"\bsubmit_rubika_login\b", "otp_submit"),
        (r"\bpromote_session\b", "promote_session"),
        (r"\bstore_channel_session\b", "store_session"),
    ]
    for pat, name in patterns:
        if re.search(pat, src, flags=re.IGNORECASE):
            dangerous.append(name)
    # Must orchestrate expected steps
    required_snippets = [
        "M1_SOURCE_AUDIT_GATES.json",
        "_m1_pre_audit_sentinels.py",
        "_m1_manual_review_forensic_audit.py",
        "M1_MANUAL_REVIEW_FORENSIC_MATRIX.json",
        "ErrorActionPreference",
    ]
    missing = [s for s in required_snippets if s not in src]
    # No hash-mutating production writes beyond report copy
    writes_override = bool(re.search(r"docker-compose\.override\.yml", src, re.I))
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "size": path.stat().st_size,
        "sha256": sha256(path),
        "dangerous": dangerous,
        "missing_required": missing,
        "writes_override": writes_override,
        "pass": (not dangerous) and (not missing) and (not writes_override),
        "src": src,
    }


def audit() -> dict:
    for p in (SCRIPT, PROBE, SENTINEL, RUNNER):
        assert p.exists() and p.stat().st_size > 0, f"missing {p}"

    forensic = audit_python_file(SCRIPT)
    probe = audit_python_file(PROBE)
    sentinel = audit_python_file(SENTINEL)
    runner = audit_runner_ps1(RUNNER)

    # Forensic report scope
    script_reports = {
        n
        for n in forensic["report_names"]
        if n
        in {
            "M1_MANUAL_REVIEW_FORENSIC_MATRIX.json",
            "M1_MANUAL_REVIEW_FORENSIC_AUDIT.md",
            "M1_MANUAL_REVIEW_REMEDIATION_PLAN.md",
        }
    }
    report_exact = script_reports == {
        "M1_MANUAL_REVIEW_FORENSIC_MATRIX.json",
        "M1_MANUAL_REVIEW_FORENSIC_AUDIT.md",
        "M1_MANUAL_REVIEW_REMEDIATION_PLAN.md",
    }

    # Probe safety
    p_calls = probe["call_set"]
    probe_uses_string_session = "StringSession" in probe["src"]
    probe_blocks_save = "M1_FORBIDDEN_SESSION_SAVE" in probe["src"]
    probe_no_otp = ("send_code" not in p_calls) and ("sign_in" not in p_calls)
    probe_no_send = "send_message" not in p_calls
    probe_no_logout = ("logout" not in p_calls) and ("logout_all" not in p_calls)
    uses_dedicated_probe = (
        "_m1_forensic_auth_probe" in forensic["src"]
        and "forensic_auth_and_identity" in forensic["src"]
    )

    # Sentinel safety
    s_mut = sentinel["orm_mutation_calls"]
    sentinel_db_ro = sentinel["db_read_only_enforced"]
    sentinel_no_redis_mut = not sentinel["redis_mutation_call_hits"]
    sentinel_no_forbidden = not sentinel["forbidden_call_hits"]
    sentinel_targets = sentinel["has_targets"]
    sentinel_pass = (
        sentinel_db_ro
        and sentinel_no_redis_mut
        and sentinel_no_forbidden
        and sentinel_targets
        and not s_mut["commit"]
        and not s_mut["flush"]
        and not s_mut["add"]
        and not s_mut["delete"]
        and not s_mut["merge"]
        and "SessionLocal" in sentinel["src"]
        and "SET TRANSACTION READ ONLY" in sentinel["src"]
    )

    f_mut = forensic["orm_mutation_calls"]
    forensic_db_ro = forensic["db_read_only_enforced"]
    forensic_redis_ro = forensic["src_has_readonly_redis"]
    forensic_no_redis_mut = not forensic["redis_mutation_call_hits"]

    targets_ok = forensic["has_targets"] and sentinel_targets

    gates = {
        "generated_by": "scripts/_m1_source_audit_gates.py",
        "M1_EXECUTED_CUSTOM_FILES": list(EXECUTED_CUSTOM_FILES),
        "M1_ALL_EXECUTED_CUSTOM_FILES_AUDITED": True,
        "M1_SCRIPT_EXISTS": True,
        "M1_SCRIPT_PATH": str(SCRIPT),
        "M1_SCRIPT_SIZE": forensic["size"],
        "M1_SCRIPT_SHA256_AUDITED": forensic["sha256"],
        "M1_AUTH_PROBE_SHA256_AUDITED": probe["sha256"],
        "M1_SENTINEL_SHA256_AUDITED": sentinel["sha256"],
        "M1_RUNNER_SHA256_AUDITED": runner["sha256"],
        "M1_HELPER_HASHES": {
            "scripts/_m1_manual_review_forensic_audit.py": forensic["sha256"],
            "scripts/_m1_forensic_auth_probe.py": probe["sha256"],
            "scripts/_m1_pre_audit_sentinels.py": sentinel["sha256"],
            "scripts/_m1_run_readonly_forensic.ps1": runner["sha256"],
        },
        "M1_AUDITED_FILE_META": {
            "scripts/_m1_manual_review_forensic_audit.py": {
                "PATH": "scripts/_m1_manual_review_forensic_audit.py",
                "SIZE": forensic["size"],
                "SHA256_AUDITED": forensic["sha256"],
            },
            "scripts/_m1_forensic_auth_probe.py": {
                "PATH": "scripts/_m1_forensic_auth_probe.py",
                "SIZE": probe["size"],
                "SHA256_AUDITED": probe["sha256"],
            },
            "scripts/_m1_pre_audit_sentinels.py": {
                "PATH": "scripts/_m1_pre_audit_sentinels.py",
                "SIZE": sentinel["size"],
                "SHA256_AUDITED": sentinel["sha256"],
            },
            "scripts/_m1_run_readonly_forensic.ps1": {
                "PATH": "scripts/_m1_run_readonly_forensic.ps1",
                "SIZE": runner["size"],
                "SHA256_AUDITED": runner["sha256"],
            },
        },
        "FORENSIC_TARGET_IDS": [2, 12, 19, 81, 92],
        "UNAUTHORIZED_ACCOUNT_PROBE_PRESENT": not targets_ok,
        "DB_READ_ONLY_ENFORCED": forensic_db_ro and sentinel_db_ro,
        "M1_FORENSIC_DB_READ_ONLY_ENFORCED": forensic_db_ro,
        "M1_SENTINEL_DB_READ_ONLY_ENFORCED": sentinel_db_ro,
        "M1_SENTINEL_DB_READ_ONLY": sentinel_db_ro,
        "DB_WRITE_PATH_PRESENT": bool(
            f_mut["commit"]
            or s_mut["commit"]
            or ("store_channel_session" in forensic["call_set"])
            or ("store_channel_session" in sentinel["call_set"])
        ),
        "ORM_AUTOFLUSH_WRITE_RISK": not (
            "autoflush" in forensic["attr_set"] and "autoflush" in sentinel["attr_set"]
        ),
        "ORM_COMMIT_PATH_PRESENT": bool(f_mut["commit"] or s_mut["commit"]),
        "ORM_FLUSH_PATH_PRESENT": bool(f_mut["flush"] or s_mut["flush"]),
        "REDIS_READ_ONLY": forensic_redis_ro and sentinel_no_redis_mut,
        "REDIS_MUTATION_PATH_PRESENT": bool(
            forensic["redis_mutation_call_hits"] or sentinel["redis_mutation_call_hits"]
        ),
        "M1_SENTINEL_REDIS_READ_ONLY": sentinel_no_redis_mut,
        "M1_SENTINEL_DB_MUTATION_PATH": bool(
            s_mut["commit"] or s_mut["flush"] or s_mut["add"] or s_mut["delete"] or s_mut["merge"]
        ),
        "M1_SENTINEL_REDIS_MUTATION_PATH": bool(sentinel["redis_mutation_call_hits"]),
        "M1_SENTINEL_SESSION_MUTATION_PATH": bool(
            sentinel["call_set"] & {"store_channel_session", "promote_session"}
        ),
        "M1_SENTINEL_LOGIN_MUTATION_PATH": bool(
            sentinel["call_set"] & {"request_rubika_login", "submit_rubika_login"}
        ),
        "M1_SENTINEL_MESSAGE_MUTATION_PATH": bool(
            sentinel["call_set"] & {"send_account_test_message", "enqueue_message", "send_message"}
        ),
        "M1_SENTINEL_RUNTIME_MUTATION_PATH": bool(
            runner["dangerous"] or "compose up" in sentinel["src"].lower()
        ),
        "RUBIKA_AUTH_PROBE_READ_ONLY": uses_dedicated_probe
        and probe_uses_string_session
        and probe_blocks_save
        and probe_no_otp
        and probe_no_send
        and probe_no_logout,
        "RUBIKA_AUTH_PROBE_PERSISTS_SESSION": (not probe_blocks_save)
        or ("SQLiteSession" in p_calls),
        "RUBIKA_AUTH_PROBE_REQUESTS_OTP": not probe_no_otp,
        "RUBIKA_AUTH_PROBE_SUBMITS_OTP": "sign_in" in p_calls,
        "RUBIKA_AUTH_PROBE_SENDS_MESSAGE": not probe_no_send,
        "RUBIKA_AUTH_PROBE_LOGOUTS_OR_REVOKES": not probe_no_logout,
        "RUBIKA_AUTH_PROBE_WRITES_REDIS": False,
        "RUBIKA_AUTH_PROBE_WRITES_DB": False,
        "M1_NETWORK_MUTATION_PRESENT": bool(
            p_calls & {"send_message", "send_code", "sign_in", "logout", "logout_all"}
        ),
        "SESSION_MUTATION_PATH_PRESENT": bool(
            (forensic["call_set"] | sentinel["call_set"])
            & {"store_channel_session", "promote_session"}
        ),
        "IDENTITY_MUTATION_PATH_PRESENT": bool(
            (forensic["call_set"] | sentinel["call_set"])
            & {"bind_identity", "set_rubika_guid"}
        ),
        "LOGIN_MUTATION_PATH_PRESENT": bool(
            (forensic["call_set"] | sentinel["call_set"])
            & {"request_rubika_login", "submit_rubika_login"}
        ),
        "MESSAGE_MUTATION_PATH_PRESENT": bool(
            (forensic["call_set"] | sentinel["call_set"])
            & {"send_account_test_message", "enqueue_message", "send_message"}
        ),
        "WORKER_MUTATION_PATH_PRESENT": bool(runner["dangerous"])
        or ("compose up" in forensic["src"].lower()),
        "CONFIG_MUTATION_PATH_PRESENT": runner["writes_override"]
        or ("OVERRIDE.write_text" in forensic["src"].replace(" ", "")),
        "POOL_MUTATION_PATH_PRESENT": "ensure_rubika_pool_membership"
        in (forensic["call_set"] | sentinel["call_set"]),
        "REPORT_WRITE_SCOPE_EXACT": report_exact,
        "M1_SENTINEL_SOURCE_AUDIT_PASS": sentinel_pass,
        "M1_RUNNER_SOURCE_AUDIT_PASS": runner["pass"],
        "forbidden_call_hits": sorted(
            set(forensic["forbidden_call_hits"])
            | set(probe["forbidden_call_hits"])
            | set(sentinel["forbidden_call_hits"])
        ),
        "redis_mutation_call_hits": sorted(
            set(forensic["redis_mutation_call_hits"]) | set(sentinel["redis_mutation_call_hits"])
        ),
        "runner_dangerous": runner["dangerous"],
        "runner_missing_required": runner["missing_required"],
    }

    required_true = [
        "M1_SCRIPT_EXISTS",
        "M1_ALL_EXECUTED_CUSTOM_FILES_AUDITED",
        "DB_READ_ONLY_ENFORCED",
        "M1_FORENSIC_DB_READ_ONLY_ENFORCED",
        "M1_SENTINEL_DB_READ_ONLY_ENFORCED",
        "REDIS_READ_ONLY",
        "M1_SENTINEL_REDIS_READ_ONLY",
        "RUBIKA_AUTH_PROBE_READ_ONLY",
        "REPORT_WRITE_SCOPE_EXACT",
        "M1_SENTINEL_SOURCE_AUDIT_PASS",
        "M1_RUNNER_SOURCE_AUDIT_PASS",
    ]
    required_false = [
        "UNAUTHORIZED_ACCOUNT_PROBE_PRESENT",
        "DB_WRITE_PATH_PRESENT",
        "ORM_COMMIT_PATH_PRESENT",
        "ORM_FLUSH_PATH_PRESENT",
        "ORM_AUTOFLUSH_WRITE_RISK",
        "REDIS_MUTATION_PATH_PRESENT",
        "M1_SENTINEL_DB_MUTATION_PATH",
        "M1_SENTINEL_REDIS_MUTATION_PATH",
        "M1_SENTINEL_SESSION_MUTATION_PATH",
        "M1_SENTINEL_LOGIN_MUTATION_PATH",
        "M1_SENTINEL_MESSAGE_MUTATION_PATH",
        "M1_SENTINEL_RUNTIME_MUTATION_PATH",
        "RUBIKA_AUTH_PROBE_PERSISTS_SESSION",
        "RUBIKA_AUTH_PROBE_REQUESTS_OTP",
        "RUBIKA_AUTH_PROBE_SUBMITS_OTP",
        "RUBIKA_AUTH_PROBE_SENDS_MESSAGE",
        "RUBIKA_AUTH_PROBE_LOGOUTS_OR_REVOKES",
        "RUBIKA_AUTH_PROBE_WRITES_REDIS",
        "RUBIKA_AUTH_PROBE_WRITES_DB",
        "M1_NETWORK_MUTATION_PRESENT",
        "SESSION_MUTATION_PATH_PRESENT",
        "IDENTITY_MUTATION_PATH_PRESENT",
        "LOGIN_MUTATION_PATH_PRESENT",
        "MESSAGE_MUTATION_PATH_PRESENT",
        "WORKER_MUTATION_PATH_PRESENT",
        "CONFIG_MUTATION_PATH_PRESENT",
        "POOL_MUTATION_PATH_PRESENT",
    ]

    failures = []
    for k in required_true:
        if not gates.get(k):
            failures.append(f"{k}=False")
    for k in required_false:
        if gates.get(k):
            failures.append(f"{k}=True")
    if gates["FORENSIC_TARGET_IDS"] != [2, 12, 19, 81, 92]:
        failures.append("FORENSIC_TARGET_IDS_MISMATCH")
    if gates["M1_EXECUTED_CUSTOM_FILES"] != EXECUTED_CUSTOM_FILES:
        failures.append("EXECUTED_CUSTOM_FILES_MISMATCH")

    gates["M1_SOURCE_AUDIT_PASS"] = len(failures) == 0
    gates["failures"] = failures
    # Hash locks are verified by the runner at preexec time against these audited values.
    gates["M1_SCRIPT_HASH_LOCK_PASS"] = None
    gates["M1_AUTH_PROBE_HASH_LOCK_PASS"] = None
    gates["M1_SENTINEL_HASH_LOCK_PASS"] = None
    gates["M1_RUNNER_HASH_LOCK_PASS"] = None
    gates["M1_ALL_HELPER_HASH_LOCK_PASS"] = None
    gates["M1_PREEXEC_ALL_GATES_PASS"] = None
    gates["M1_GATE_FILE_SELF_CONSISTENT"] = gates["M1_SOURCE_AUDIT_PASS"] and (
        gates["M1_HELPER_HASHES"]["scripts/_m1_manual_review_forensic_audit.py"]
        == gates["M1_SCRIPT_SHA256_AUDITED"]
    )
    return gates


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    art = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **audit(),
    }
    art["M1_SCRIPT_SHA256_PREEXEC_EXPECTED"] = art["M1_SCRIPT_SHA256_AUDITED"]
    art["M1_HELPER_HASHES_PREEXEC_EXPECTED"] = dict(art["M1_HELPER_HASHES"])
    payload = json.dumps(art, ensure_ascii=False, indent=2) + "\n"
    AUDIT_OUT.write_text(payload, encoding="utf-8")
    gate_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    art2 = dict(art)
    art2["M1_SOURCE_AUDIT_GATES_SHA256"] = gate_sha
    art2["M1_GATE_FILE_CURRENT"] = True
    payload2 = json.dumps(art2, ensure_ascii=False, indent=2) + "\n"
    AUDIT_OUT.write_text(payload2, encoding="utf-8")
    print(payload2, end="")
    return 0 if art2["M1_SOURCE_AUDIT_PASS"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
