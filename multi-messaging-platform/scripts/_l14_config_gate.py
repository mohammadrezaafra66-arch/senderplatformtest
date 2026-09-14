#!/usr/bin/env python3
"""L14 config gate: current cohort expanded; rollback remains L13."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUR = ROOT / "docker-compose.override.yml"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def vals(text: str, key: str) -> list[str]:
    return re.findall(rf'^\s*{re.escape(key)}:\s*"([^"]*)"\s*$', text, flags=re.M)


def main() -> int:
    baks = sorted(ROOT.glob("docker-compose.override.yml.before-l14-cohort-expansion-*.bak"))
    if not baks:
        print("GATE_ERROR=NO_ROLLBACK")
        return 1
    rb = baks[-1]
    if rb.stat().st_size <= 0:
        print("GATE_ERROR=ROLLBACK_EMPTY")
        return 1
    c = CUR.read_text(encoding="utf-8")
    r = rb.read_text(encoding="utf-8")
    ok = (
        vals(c, "RUBIKA_WORKER_DISCOVERY_MODE") == ["dynamic"]
        and vals(c, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == ["13,23,74"]
        and vals(c, "RUBIKA_ACCOUNT_IDS") == ["12,79"]
        and set(vals(c, "RUBIKA_CANONICAL_SESSION_MODE")) == {"shadow"}
        and set(vals(c, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS")) == {"13,23,74"}
        and vals(r, "RUBIKA_WORKER_DISCOVERY_MODE") == ["dynamic"]
        and vals(r, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == ["13"]
        and vals(r, "RUBIKA_ACCOUNT_IDS") == ["12,79"]
        and sha(CUR) != sha(rb)
    )
    print(f"ROLLBACK_FILE={rb.name}")
    print(f"ROLLBACK_SHA256={sha(rb)}")
    print(f"CURRENT_SHA256={sha(CUR)}")
    print(f"CURRENT_COHORT={vals(c, 'RUBIKA_WORKER_DISCOVERY_COHORT_IDS')}")
    print(f"ROLLBACK_COHORT={vals(r, 'RUBIKA_WORKER_DISCOVERY_COHORT_IDS')}")
    print(f"CONFIG_GATE_PASS={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
