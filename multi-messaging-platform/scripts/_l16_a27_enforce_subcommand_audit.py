#!/usr/bin/env python3
"""Source-audit L16 Account27 ENFORCE subcommands (no production mutation)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "_l16_a27_rollout.py").read_text(encoding="utf-8")


def _extract_cmd_block(cmd: str) -> str:
    pat = rf'if cmd == "{re.escape(cmd)}":'
    m = re.search(pat, SRC)
    if not m:
        return ""
    start = m.start()
    rest = SRC[m.end() :]
    nxt = re.search(r'if cmd == "', rest)
    end = m.end() + (nxt.start() if nxt else len(rest))
    return SRC[start:end]


def audit() -> dict:
    backup = _extract_cmd_block("backup-enforce")
    setc = _extract_cmd_block("set-enforce")
    recre = _extract_cmd_block("recreate-consumers")
    roll = _extract_cmd_block("rollback-enforce")

    set_keys = re.findall(r'set_key\("([^"]+)"', setc)
    enforce_exact = (
        set_keys == ["RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS"]
        and 'set_key("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", "13,23,27,74")' in setc
        and '== {"13,23,27,74"}' in setc
        and '== {"enforce"}' in setc
        and '== ["13,23,27,74"]' in setc  # cohort preserved
        and '== ["12,79"]' in setc
        and '== ["dynamic"]' in setc
        and "compose_recreate" not in setc
    )

    worker_mutated = bool(
        re.search(r'set_key\("RUBIKA_WORKER_', setc + backup + recre)
    ) or bool(re.search(r'set_key\("RUBIKA_ACCOUNT_IDS"', setc + backup + recre))

    pin_mutated = 'set_key("RUBIKA_ACCOUNT_IDS"' in (setc + backup + recre)

    backup_safe = (
        'backup("a27-enforce")' in backup
        and '== ["13,23,27,74"]' in backup
        and '== {"13,23,74"}' in backup
        and "set_key" not in backup
        and "compose_recreate" not in backup
        and "rollback_hard_gate_pass" in backup
    )

    recre_exact = (
        'compose_recreate("core_api", "rubika_worker")' in recre
        and "--build" not in SRC[SRC.find("def compose_recreate") : SRC.find("def worker_probe")]
        and 'assert "postgres" not in out["cmd"]' in recre
        and 'assert "redis" not in out["cmd"]' in recre
        and 'assert "--build" not in out["cmd"]' in recre
        and "set_key" not in recre
        and 'compose_recreate("postgres"' not in recre
        and 'compose_recreate("redis"' not in recre
    )

    # Full file enforce path must not OTP/send/migrate/session SQL
    enforce_paths = backup + setc + recre + roll
    no_otp = "request_otp" not in enforce_paths.lower() and "submit_otp" not in enforce_paths.lower()
    no_send = "message_attempt" not in enforce_paths.lower() and "enqueue" not in enforce_paths.lower()
    no_mig = "alembic" not in enforce_paths.lower() and "migration" not in enforce_paths.lower()
    no_session_sql = not re.search(
        r"\b(INSERT|UPDATE|DELETE)\b.*channel_sessions", enforce_paths, re.I
    )

    return {
        "ENFORCE_MUTATION_SCOPE_EXACT": enforce_exact and backup_safe and recre_exact,
        "WORKER_CONFIG_MUTATED_BY_ENFORCE": worker_mutated,
        "PIN_MUTATED_BY_ENFORCE": pin_mutated,
        "SESSION_STATE_DIRECTLY_MUTATED": not no_session_sql,
        "SCRIPT_REQUESTS_OTP": not no_otp,
        "SCRIPT_SENDS_MESSAGES": not no_send,
        "SCRIPT_RUNS_MIGRATIONS": not no_mig,
        "BACKUP_ENFORCE_SCOPE_SAFE": backup_safe,
        "SET_ENFORCE_TARGET_EXACT": enforce_exact,
        "RECREATE_CONSUMERS_SCOPE_EXACT": recre_exact,
        "set_key_calls_in_set_enforce": set_keys,
        "ok": (
            enforce_exact
            and backup_safe
            and recre_exact
            and not worker_mutated
            and not pin_mutated
            and no_otp
            and no_send
            and no_mig
            and no_session_sql
        ),
    }


if __name__ == "__main__":
    art = audit()
    print(json.dumps(art, indent=2))
    raise SystemExit(0 if art["ok"] else 2)
