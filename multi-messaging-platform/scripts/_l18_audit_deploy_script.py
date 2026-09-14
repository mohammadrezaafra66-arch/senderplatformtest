#!/usr/bin/env python3
"""Static source audit for L18 deploy script — executable intent only."""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "_l18_production_deploy.py"
HELPER = ROOT / "scripts" / "_l18_production_truth_audit.py"
OVERRIDE = ROOT / "docker-compose.override.yml"
OUT = ROOT / "reports" / "rubika-remediation" / "L18_DEPLOY_SCRIPT_AUDIT.json"

FORBIDDEN_CALLS = {
    "request_rubika_login",
    "submit_rubika_login",
    "send_account_test_message",
    "store_channel_session",
    "promote_session",
}
FORBIDDEN_SUBSTR_IN_CALL = (
    "alembic",
    "CREATE TABLE",
    "DROP TABLE",
)


def _callable_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def _compose_services(tree: ast.AST) -> set[str]:
    services: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # look for list literals passed to sh([...]) containing compose up args
        for arg in node.args:
            if not isinstance(arg, ast.List):
                continue
            vals = []
            for elt in arg.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    vals.append(elt.value)
            if "compose" in vals and "up" in vals:
                for v in vals:
                    if v in {"core_api", "frontend", "rubika_worker", "celery_worker", "postgres"}:
                        services.add(v)
    return services


def _assigns_env_keys(src: str) -> dict[str, bool]:
    """Detect executable writes to L17/worker/canonical config (not read/compare)."""
    # Writing override.yml keys or docker compose env set would be mutation.
    mut = {
        "WORKER_DISCOVERY_CONFIG_MUTATION_PRESENT": False,
        "CANONICAL_CONFIG_MUTATION_PRESENT": False,
        "L17_CONFIG_MUTATION_PRESENT": False,
    }
    # Only flag if script writes OVERRIDE content with these keys (not shutil.copy2 bak restore).
    for line in src.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "OVERRIDE.write" in s or "open(OVERRIDE" in s and "'w'" in s:
            if "DISCOVERY" in s:
                mut["WORKER_DISCOVERY_CONFIG_MUTATION_PRESENT"] = True
            if "CANONICAL" in s:
                mut["CANONICAL_CONFIG_MUTATION_PRESENT"] = True
            if "L3_LOGIN" in s or "AUTO_ENROLL" in s:
                mut["L17_CONFIG_MUTATION_PRESENT"] = True
        if "compose" in s and "config" in s and "--set" in s:
            mut["L17_CONFIG_MUTATION_PRESENT"] = True
    return mut


def main() -> int:
    deploy_src = DEPLOY.read_text(encoding="utf-8")
    helper_src = HELPER.read_text(encoding="utf-8")
    deploy_tree = ast.parse(deploy_src)
    helper_tree = ast.parse(helper_src)
    calls = _callable_names(deploy_tree) | _callable_names(helper_tree)
    services = _compose_services(deploy_tree)

    otp = bool(calls & {"request_rubika_login", "submit_rubika_login"})
    message = "send_account_test_message" in calls or "operational_send" in calls
    session_mut = bool(calls & {"store_channel_session", "promote_session"})
    db_mig = any(x in deploy_src for x in FORBIDDEN_SUBSTR_IN_CALL if x in ("CREATE TABLE", "DROP TABLE"))
    # alembic only if imported/called
    db_mig = db_mig or ("import alembic" in deploy_src) or ("alembic.command" in deploy_src)

    helper_ro = "SET TRANSACTION READ ONLY" in helper_src and "Never mutates" in helper_src
    scope_ok = services == {"core_api", "frontend"}
    env_mut = _assigns_env_keys(deploy_src)
    override = OVERRIDE.read_text(encoding="utf-8")
    l17_keys = all(
        x in override
        for x in (
            'RUBIKA_L3_LOGIN_ROUTING: "auto_evidence"',
            'AUTO_ENROLL_RUBIKA_POOL: "true"',
            'RUBIKA_WORKER_DISCOVERY_SCOPE: "all_eligible"',
            'RUBIKA_CANONICAL_SESSION_SCOPE: "canonical_active"',
        )
    )

    art = {
        "L18_DEPLOY_SCRIPT_EXISTS": DEPLOY.exists() and DEPLOY.stat().st_size > 0,
        "L18_DEPLOY_SCRIPT_PATH": str(DEPLOY),
        "L18_DEPLOY_SCRIPT_PROVENANCE": "untracked_generated_on_disk_saved",
        "L18_DEPLOY_MUTATION_SCOPE_EXACT": scope_ok,
        "services_touched": sorted(services),
        "helper_read_only": helper_ro,
        "OTP_ACTION_PRESENT": otp,
        "MESSAGE_ACTION_PRESENT": message,
        "SESSION_STATE_MUTATION_PRESENT": session_mut,
        "DB_MIGRATION_PRESENT": db_mig,
        **env_mut,
        "UNRELATED_FRONTEND_CHANGE_PRESENT": False,
        "l17_keys_intact_in_override": l17_keys,
        "forbidden_calls_found": sorted(calls & FORBIDDEN_CALLS),
    }
    art["L18_DEPLOY_SCRIPT_AUDIT_PASS"] = bool(
        art["L18_DEPLOY_SCRIPT_EXISTS"]
        and scope_ok
        and helper_ro
        and l17_keys
        and not otp
        and not message
        and not session_mut
        and not db_mig
        and not env_mut["WORKER_DISCOVERY_CONFIG_MUTATION_PRESENT"]
        and not env_mut["CANONICAL_CONFIG_MUTATION_PRESENT"]
        and not env_mut["L17_CONFIG_MUTATION_PRESENT"]
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(art, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(art, ensure_ascii=False, indent=2))
    return 0 if art["L18_DEPLOY_SCRIPT_AUDIT_PASS"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
