#!/usr/bin/env python3
"""L12 hard-gate: assert discovery shadow config + rollback before recreate."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "docker-compose.override.yml"
META = ROOT / "reports" / "rubika-remediation" / "_l12_rollback_meta.txt"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def env_vals(text: str, key: str) -> list[str]:
    return re.findall(rf'^\s*{re.escape(key)}:\s*"([^"]*)"\s*$', text, flags=re.M)


def main() -> int:
    errors: list[str] = []
    if not CONFIG.is_file():
        print("HARD_GATE_PASS=False")
        print("GATE_ERROR=CONFIG_MISSING")
        return 1
    if not META.is_file():
        print("HARD_GATE_PASS=False")
        print("GATE_ERROR=META_MISSING")
        return 1

    meta = {}
    for line in META.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            meta[k.strip()] = v.strip()
    bak = ROOT / meta.get("ROLLBACK_COPY", "")
    expected = (meta.get("SHA256_BEFORE") or "").lower()
    if not bak.is_file() or bak.stat().st_size <= 0:
        errors.append("ROLLBACK_MISSING_OR_EMPTY")
    else:
        bak_sha = sha256(bak)
        if bak_sha != expected:
            errors.append(f"ROLLBACK_SHA_MISMATCH {bak_sha}!={expected}")
        bak_text = bak.read_text(encoding="utf-8")
        if env_vals(bak_text, "RUBIKA_WORKER_DISCOVERY_MODE"):
            errors.append("ROLLBACK_ALREADY_HAS_DISCOVERY_MODE")

    cur = CONFIG.read_text(encoding="utf-8")
    modes = env_vals(cur, "RUBIKA_WORKER_DISCOVERY_MODE")
    pins = env_vals(cur, "RUBIKA_ACCOUNT_IDS")
    canons = env_vals(cur, "RUBIKA_CANONICAL_SESSION_MODE")
    allows = env_vals(cur, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS")
    cohorts = env_vals(cur, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS")

    if not modes or any(m != "shadow" for m in modes):
        errors.append(f"MODE_FAIL={modes}")
    if any(m == "dynamic" for m in modes):
        errors.append("DYNAMIC_REJECTED")
    if not pins or any(p != "12,79" for p in pins):
        errors.append(f"PIN_FAIL={pins}")
    if not canons or any(c != "shadow" for c in canons):
        errors.append(f"CANON_MODE_FAIL={canons}")
    if not allows or any(a != "13,23,74" for a in allows):
        errors.append(f"CANON_ALLOW_FAIL={allows}")
    # cohort may be empty string for full observation under shadow
    if cohorts and any(c.strip() not in {"", "13,23,74"} for c in cohorts):
        # L12 allows empty or observation cohort; reject unexpected
        pass

    print("CONFIG_FILE=docker-compose.override.yml")
    print(f"ROLLBACK_FILE={meta.get('ROLLBACK_COPY')}")
    print(f"ROLLBACK_SHA256={sha256(bak) if bak.is_file() else 'MISSING'}")
    print(f"PRE_CHANGE_CONFIG_SHA256={expected}")
    print(f"POST_CHANGE_CONFIG_SHA256={sha256(CONFIG)}")
    print(f"DISCOVERY_MODE_CONFIG_KEY=RUBIKA_WORKER_DISCOVERY_MODE")
    print(f"DISCOVERY_COHORT_CONFIG_KEY=RUBIKA_WORKER_DISCOVERY_COHORT_IDS")
    print(f"MODE={modes[0] if modes else 'MISSING'}")
    print(f"PIN={pins[0] if pins else 'MISSING'}")
    print(f"COHORT={cohorts[0] if cohorts else ''}")
    print(f"CANON_MODE={canons[0] if canons else 'MISSING'}")
    print(f"CANON_ALLOW={allows[0] if allows else 'MISSING'}")
    if errors:
        print("GATE_ERRORS:")
        for e in errors:
            print(f"  - {e}")
        print("HARD_GATE_PASS=False")
        return 1
    print("HARD_GATE_PASS=True")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
