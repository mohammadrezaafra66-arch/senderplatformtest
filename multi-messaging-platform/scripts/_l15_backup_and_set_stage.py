#!/usr/bin/env python3
"""L15: backup override + set canonical enforce stage (mode + allowlist only)."""
from __future__ import annotations

import hashlib
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERRIDE = ROOT / "docker-compose.override.yml"
REPORTS = ROOT / "reports" / "rubika-remediation"


def _vals(text: str, key: str) -> list[str]:
    return re.findall(
        rf'^\s*{re.escape(key)}:\s*"([^"]*)"\s*$',
        text,
        flags=re.M,
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup_shadow() -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rb = ROOT / f"docker-compose.override.yml.before-l15-enforce-{ts}.bak"
    shutil.copy2(OVERRIDE, rb)
    t = rb.read_text(encoding="utf-8")
    modes = set(_vals(t, "RUBIKA_CANONICAL_SESSION_MODE"))
    allows = set(_vals(t, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS"))
    assert modes == {"shadow"}, modes
    assert allows == {"13,23,74"}, allows
    assert _vals(t, "RUBIKA_WORKER_DISCOVERY_MODE") == ["dynamic"]
    assert _vals(t, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == ["13,23,74"]
    assert _vals(t, "RUBIKA_ACCOUNT_IDS") == ["12,79"]
    assert rb.stat().st_size > 0
    h = _sha(rb)
    meta = REPORTS / "_l15_rollback_meta.txt"
    meta.write_text(
        f"ROLLBACK_COPY={rb.name}\n"
        f"SHA256={h}\n"
        f"SIZE={rb.stat().st_size}\n"
        f"STAGE=shadow_full_batch_a\n"
        f"MODE=shadow\n"
        f"ALLOWLIST=13,23,74\n"
        f"DISCOVERY=dynamic\n"
        f"COHORT=13,23,74\n"
        f"PIN=12,79\n",
        encoding="ascii",
    )
    return {
        "rollback_file": str(rb),
        "sha256": h,
        "size": rb.stat().st_size,
        "ok": True,
    }


def set_canonical_stage(mode: str, allowlist: str) -> dict:
    """Replace only canonical mode/allowlist values in override (all occurrences)."""
    text = OVERRIDE.read_text(encoding="utf-8")
    text2, n1 = re.subn(
        r'^(\s*RUBIKA_CANONICAL_SESSION_MODE:\s*")[^"]*("\s*)$',
        rf"\g<1>{mode}\g<2>",
        text,
        flags=re.M,
    )
    text3, n2 = re.subn(
        r'^(\s*RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS:\s*")[^"]*("\s*)$',
        rf"\g<1>{allowlist}\g<2>",
        text2,
        flags=re.M,
    )
    if n1 < 1 or n2 < 1:
        raise SystemExit(f"replace failed n1={n1} n2={n2}")
    # discovery / pin must be unchanged
    assert _vals(text3, "RUBIKA_WORKER_DISCOVERY_MODE") == ["dynamic"]
    assert _vals(text3, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == ["13,23,74"]
    assert _vals(text3, "RUBIKA_ACCOUNT_IDS") == ["12,79"]
    modes = set(_vals(text3, "RUBIKA_CANONICAL_SESSION_MODE"))
    allows = set(_vals(text3, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS"))
    assert modes == {mode}, modes
    assert allows == {allowlist}, allows
    OVERRIDE.write_text(text3, encoding="utf-8")
    return {
        "mode": mode,
        "allowlist": allowlist,
        "replacements_mode": n1,
        "replacements_allow": n2,
        "sha256": _sha(OVERRIDE),
    }


def restore_from(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size <= 0:
        raise SystemExit(f"bad rollback file: {path}")
    shutil.copy2(path, OVERRIDE)
    return {"restored_from": str(path), "sha256": _sha(OVERRIDE)}


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: backup | set MODE ALLOWLIST | restore PATH")
        return 2
    cmd = sys.argv[1]
    if cmd == "backup":
        print(backup_shadow())
        return 0
    if cmd == "set":
        print(set_canonical_stage(sys.argv[2], sys.argv[3]))
        return 0
    if cmd == "restore":
        print(restore_from(Path(sys.argv[2])))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
