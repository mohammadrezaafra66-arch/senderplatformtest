#!/usr/bin/env python3
"""Source-audit the L16 Account27 worker-canary subcommands (no production mutation)."""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "scripts" / "_l16_a27_rollout.py").read_text(encoding="utf-8")


def _extract_cmd_block(cmd: str) -> str:
    """Return source between `if cmd == "<cmd>":` and the next `if cmd ==`."""
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
    probe = _extract_cmd_block("worker-probe")
    backup = _extract_cmd_block("backup-cohort")
    setc = _extract_cmd_block("set-cohort")
    recre = _extract_cmd_block("recreate-worker")

    # worker-probe must only call worker_probe() / print
    worker_probe_ro = (
        "worker_probe()" in probe
        and "set_key" not in probe
        and "compose_recreate" not in probe
        and "OVERRIDE.write" not in probe
        and "backup(" not in probe
        and "restore(" not in probe
    )

    backup_safe = (
        'backup("a27-cohort")' in backup
        and 'RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == ["13,23,74"]' in backup
        and 'RUBIKA_WORKER_DISCOVERY_MODE") == ["dynamic"]' in backup
        and 'RUBIKA_ACCOUNT_IDS") == ["12,79"]' in backup
        and 'RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS")) == {"13,23,74"}' in backup
        and "set_key" not in backup
        and "compose_recreate" not in backup
        and "OVERRIDE.write" not in backup
    )

    set_exact = (
        'set_key("RUBIKA_WORKER_DISCOVERY_COHORT_IDS", "13,23,27,74")' in setc
        and 'RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == ["13,23,27,74"]' in setc
        and 'RUBIKA_ACCOUNT_IDS") == ["12,79"]' in setc
        and 'RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS")) == {"13,23,74"}' in setc
        and 'RUBIKA_WORKER_DISCOVERY_MODE") == ["dynamic"]' in setc
        and 'RUBIKA_CANONICAL_SESSION_MODE")) == {"enforce"}' in setc
        and "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS" not in setc.split("set_key")[1].split("\n")[0]
        and "compose_recreate" not in setc
        and "core_api" not in setc
    )

    # set_key must only be invoked for cohort key in this block
    set_key_calls = re.findall(r'set_key\("([^"]+)"', setc)
    set_exact = set_exact and set_key_calls == ["RUBIKA_WORKER_DISCOVERY_COHORT_IDS"]

    recre_exact = (
        'compose_recreate("rubika_worker")' in recre
        and 'compose_recreate("core_api"' not in recre
        and "celery" not in recre
        and "set_key" not in recre
        and "OVERRIDE.write" not in recre
        and '"--force-recreate",\n            "rubika_worker"' in recre.replace("\r\n", "\n")
        and 'assert "core_api" not in out["cmd"]' in recre
    )

    # compose_recreate helper must never --build
    compose_helper = SRC[SRC.find("def compose_recreate") : SRC.find("def worker_probe")]
    compose_no_build = (
        "--build" not in compose_helper
        and "--force-recreate" in compose_helper
        and "--no-deps" in compose_helper
    )

    # Canonical keys never written by the four worker subcommands
    worker_cmds = probe + backup + setc + recre
    canon_mutated = bool(
        re.search(
            r'set_key\("RUBIKA_CANONICAL_SESSION_(MODE|ACCOUNT_IDS)"',
            worker_cmds,
        )
    )

    mutation_scope = (
        set_exact
        and not canon_mutated
        and backup_safe
        and recre_exact
        and worker_probe_ro
    )

    return {
        "WORKER_PROBE_READ_ONLY": worker_probe_ro,
        "BACKUP_COHORT_SCOPE_SAFE": backup_safe,
        "SET_COHORT_TARGET_EXACT": set_exact,
        "RECREATE_WORKER_SCOPE_EXACT": recre_exact and compose_no_build,
        "WORKER_ROLLOUT_MUTATION_SCOPE_EXACT": mutation_scope,
        "CANONICAL_CONFIG_MUTATED_BY_WORKER_SUBCOMMANDS": canon_mutated,
        "set_key_calls_in_set_cohort": set_key_calls,
        "ok": (
            worker_probe_ro
            and backup_safe
            and set_exact
            and recre_exact
            and compose_no_build
            and not canon_mutated
            and mutation_scope
        ),
    }


if __name__ == "__main__":
    art = audit()
    print(json.dumps(art, indent=2))
    raise SystemExit(0 if art["ok"] else 2)
