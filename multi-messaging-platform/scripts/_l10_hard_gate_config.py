#!/usr/bin/env python3
"""L10 hard-gate: assert override config + rollback before any recreate.

Exit 0 only when all assertions pass. Prints secret-safe provenance only.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "docker-compose.override.yml"
META = ROOT / "reports" / "rubika-remediation" / "_l10_rollback_meta.txt"
ALLOWED_MODE = "shadow"
ALLOWED_ALLOWLIST = frozenset({"13", "23", "74"})
ALLOWED_PIN = frozenset({"12", "79"})


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_env_strings(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for m in re.finditer(
        r'^\s{2,}([A-Z0-9_]+):\s*"([^"]*)"\s*$', text, flags=re.MULTILINE
    ):
        found.setdefault(m.group(1), []).append(m.group(2))
    return found


def _csv_set(value: str) -> set[str]:
    return {p.strip() for p in value.split(",") if p.strip()}


def main() -> int:
    errors: list[str] = []

    if CONFIG.name != "docker-compose.override.yml" or not CONFIG.is_file():
        print("CONFIG_FILE=docker-compose.override.yml")
        print("HARD_GATE_PASS=False")
        print("GATE_ERROR=CONFIG_FILE_MISSING_OR_WRONG")
        return 1

    if not META.is_file():
        print("CONFIG_FILE=docker-compose.override.yml")
        print("HARD_GATE_PASS=False")
        print("GATE_ERROR=ROLLBACK_META_MISSING")
        return 1

    meta: dict[str, str] = {}
    for line in META.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            meta[k.strip().lstrip("\ufeff")] = v.strip()

    bak_name = meta.get("ROLLBACK_COPY", "")
    expected_before = (meta.get("SHA256_BEFORE") or "").lower()
    bak = ROOT / bak_name if bak_name else None

    rb_pass = True
    if not bak or not bak.is_file():
        errors.append("ROLLBACK_FILE_MISSING")
        rb_pass = False
    elif bak.stat().st_size <= 0:
        errors.append("ROLLBACK_FILE_EMPTY")
        rb_pass = False
    else:
        bak_sha = _sha256(bak)
        if not expected_before or bak_sha != expected_before:
            errors.append(
                f"ROLLBACK_SHA_MISMATCH expected={expected_before} actual={bak_sha}"
            )
            rb_pass = False
        bak_env = _parse_env_strings(bak.read_text(encoding="utf-8"))
        if "RUBIKA_CANONICAL_SESSION_MODE" in bak_env:
            errors.append("ROLLBACK_HAS_CANONICAL_MODE")
            rb_pass = False
        if "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS" in bak_env:
            errors.append("ROLLBACK_HAS_CANONICAL_ALLOWLIST")
            rb_pass = False
        pins = bak_env.get("RUBIKA_ACCOUNT_IDS", [])
        if not pins or any(_csv_set(p) != ALLOWED_PIN for p in pins):
            errors.append(f"ROLLBACK_PIN_UNEXPECTED={pins}")
            rb_pass = False

    cur = CONFIG.read_text(encoding="utf-8")
    cur_sha = _sha256(CONFIG)
    env = _parse_env_strings(cur)
    modes = env.get("RUBIKA_CANONICAL_SESSION_MODE", [])
    allows = env.get("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", [])
    pins = env.get("RUBIKA_ACCOUNT_IDS", [])

    val_pass = True
    if not modes or any(m != ALLOWED_MODE for m in modes):
        errors.append(f"MODE_ASSERT_FAIL values={modes}")
        val_pass = False
    if any(str(m).lower() == "enforce" for m in modes) or re.search(
        r"RUBIKA_CANONICAL_SESSION_MODE:\s*\"?enforce\"?", cur, re.I
    ):
        errors.append("ENFORCE_REJECTED")
        val_pass = False
    if not allows:
        errors.append("ALLOWLIST_MISSING")
        val_pass = False
    else:
        for a in allows:
            s = _csv_set(a)
            if s != ALLOWED_ALLOWLIST:
                errors.append(f"ALLOWLIST_ASSERT_FAIL value={a}")
                val_pass = False
    if not pins:
        errors.append("WORKER_PIN_MISSING")
        val_pass = False
    else:
        for p in pins:
            if _csv_set(p) != ALLOWED_PIN:
                errors.append(f"WORKER_PIN_ASSERT_FAIL value={p}")
                val_pass = False
    if len(modes) < 2 or len(allows) < 2:
        errors.append(
            f"INCOMPLETE_SERVICE_COVERAGE modes={len(modes)} allows={len(allows)}"
        )
        val_pass = False

    print("CONFIG_FILE=docker-compose.override.yml")
    print(f"ROLLBACK_FILE={bak_name or 'MISSING'}")
    print(
        f"ROLLBACK_SHA256={_sha256(bak) if bak and bak.is_file() else 'MISSING'}"
    )
    print(f"CURRENT_CONFIG_SHA256={cur_sha}")
    print(f"MODE={modes[0] if modes else 'MISSING'}")
    print(f"ALLOWLIST={allows[0] if allows else 'MISSING'}")
    print(f"WORKER_PIN={pins[0] if pins else 'MISSING'}")
    print(f"CONFIG_ROLLBACK_GATE_PASS={str(rb_pass).upper()}")
    print(f"CONFIG_EXACT_VALUE_GATE_PASS={str(val_pass).upper()}")

    if errors:
        print("GATE_ERRORS:")
        for e in errors:
            print(f"  - {e}")
        print("HARD_GATE_PASS=False")
        return 1

    print("HARD_GATE_PASS=True")
    return 0


if __name__ == "__main__":
    sys.exit(main())
