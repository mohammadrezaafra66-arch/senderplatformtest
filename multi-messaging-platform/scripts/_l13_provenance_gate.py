#!/usr/bin/env python3
"""L13 provenance gate — exit 0 only when rollback + canary config assert."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CUR = ROOT / "docker-compose.override.yml"
RB = ROOT / "docker-compose.override.yml.l13-rollback-to-l12-shadow.bak"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def vals(text: str, key: str) -> list[str]:
    return re.findall(
        rf'^\s*{re.escape(key)}:\s*"([^"]*)"\s*$', text, flags=re.M
    )


def main() -> int:
    if not RB.is_file() or RB.stat().st_size <= 0:
        print("ROLLBACK_PROVENANCE_PASS=False")
        print("GATE_ERROR=ROLLBACK_MISSING")
        return 1
    if not CUR.is_file():
        print("ROLLBACK_PROVENANCE_PASS=False")
        print("GATE_ERROR=CURRENT_MISSING")
        return 1

    rb = RB.read_text(encoding="utf-8")
    cur = CUR.read_text(encoding="utf-8")

    rb_mode_ok = vals(rb, "RUBIKA_WORKER_DISCOVERY_MODE") == ["shadow"]
    rb_cohort_ok = vals(rb, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == [""]
    rb_pin_ok = vals(rb, "RUBIKA_ACCOUNT_IDS") == ["12,79"]
    rb_canon_ok = set(vals(rb, "RUBIKA_CANONICAL_SESSION_MODE")) == {"shadow"}
    rb_allow_ok = set(vals(rb, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS")) == {
        "13,23,74"
    }

    cur_mode_ok = vals(cur, "RUBIKA_WORKER_DISCOVERY_MODE") == ["dynamic"]
    cur_cohort_ok = vals(cur, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == ["13"]
    cur_pin_ok = vals(cur, "RUBIKA_ACCOUNT_IDS") == ["12,79"]
    cur_canon_ok = set(vals(cur, "RUBIKA_CANONICAL_SESSION_MODE")) == {"shadow"}
    cur_allow_ok = set(vals(cur, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS")) == {
        "13,23,74"
    }

    sha_rb = sha(RB)
    sha_cur = sha(CUR)
    distinct = sha_rb != sha_cur

    print(f"ROLLBACK_DISCOVERY_MODE_SHADOW={rb_mode_ok}")
    print(f"ROLLBACK_DISCOVERY_COHORT_EMPTY={rb_cohort_ok}")
    print(f"ROLLBACK_PIN_12_79={rb_pin_ok}")
    print(f"ROLLBACK_CANONICAL_MODE_SHADOW={rb_canon_ok}")
    print(f"ROLLBACK_CANONICAL_ALLOWLIST_13_23_74={rb_allow_ok}")
    print(f"CURRENT_DISCOVERY_MODE_DYNAMIC={cur_mode_ok}")
    print(f"CURRENT_DISCOVERY_COHORT_13={cur_cohort_ok}")
    print(f"CURRENT_PIN_12_79={cur_pin_ok}")
    print(f"CURRENT_CANONICAL_MODE_SHADOW={cur_canon_ok}")
    print(f"CURRENT_CANONICAL_ALLOWLIST_13_23_74={cur_allow_ok}")
    print(f"ROLLBACK_SHA256={sha_rb}")
    print(f"CURRENT_SHA256={sha_cur}")
    print(f"ROLLBACK_SIZE={RB.stat().st_size}")
    print(f"SHA_DISTINCT={distinct}")

    current_exact = all(
        [cur_mode_ok, cur_cohort_ok, cur_pin_ok, cur_canon_ok, cur_allow_ok]
    )
    rollback_ok = all(
        [rb_mode_ok, rb_cohort_ok, rb_pin_ok, rb_canon_ok, rb_allow_ok]
    )
    prov = rollback_ok and current_exact and distinct
    print(f"CURRENT_CANARY_CONFIG_EXACT={current_exact}")
    print(f"ROLLBACK_PROVENANCE_PASS={prov}")
    return 0 if prov else 1


if __name__ == "__main__":
    raise SystemExit(main())
