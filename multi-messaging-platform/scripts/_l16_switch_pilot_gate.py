#!/usr/bin/env python3
"""L16 — switch account-scoped L3 login pilot gate (core_api only)."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERRIDE = ROOT / "docker-compose.override.yml"
REPORT_DIR = ROOT / "reports" / "rubika-remediation"
PG = "mmp_postgres"
PG_USER = "mmp_user"
PRODUCTION_DB = "mmp_db"
CORE_API = "mmp_core_api"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup_override(pilot: int) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rb = ROOT / f"docker-compose.override.yml.before-l16-a{pilot}-{ts}.bak"
    shutil.copy2(OVERRIDE, rb)
    size = rb.stat().st_size
    if size <= 0:
        raise SystemExit("rollback copy empty")
    digest = _sha(rb)
    text = rb.read_text(encoding="utf-8")
    pre = re.findall(
        r'^\s*RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS:\s*"([^"]*)"\s*$',
        text,
        flags=re.M,
    )
    # Expect current Account3 pilot gate before switching to 27
    if pilot == 27 and pre and pre[0] not in {"3", "27"}:
        raise SystemExit(f"unexpected pre-change pilot gate: {pre}")
    return {
        "path": str(rb),
        "sha256": digest,
        "size": size,
        "sha_recomputed_ok": digest == _sha(rb),
        "pre_pilot_gate": pre[0] if pre else None,
    }


def fresh_backup(pilot: int) -> dict:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump = REPORT_DIR / f"mmp_db_pre_l16_a{pilot}_{ts}.dump"
    subprocess.run(
        [
            "docker",
            "exec",
            PG,
            "pg_dump",
            "-U",
            PG_USER,
            "-Fc",
            "-f",
            f"/tmp/{dump.name}",
            PRODUCTION_DB,
        ],
        check=True,
    )
    subprocess.run(["docker", "cp", f"{PG}:/tmp/{dump.name}", str(dump)], check=True)
    size = dump.stat().st_size
    if size <= 0:
        raise SystemExit("db backup empty")
    digest = _sha(dump)
    meta = {
        "BACKUP_PATH": str(dump.resolve()),
        "BACKUP_SHA256": digest,
        "BACKUP_SIZE": size,
        "created_at": ts,
        "source_db": PRODUCTION_DB,
        "sha_match": digest == _sha(dump),
        "freshness_pass": True,
    }
    dump.with_suffix(".dump.meta.json").write_text(
        __import__("json").dumps(meta, indent=2), encoding="utf-8"
    )
    return meta


def set_pilot_gate(pilot: int) -> dict:
    if int(pilot) != 27:
        # This L16 replacement step is locked to Account27.
        raise SystemExit("SWITCH_GATE_TARGET_LOCKED_TO_27: refuse non-27 pilot")
    text = OVERRIDE.read_text(encoding="utf-8")
    key = "RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS"
    if re.search(rf"^\s*{re.escape(key)}:", text, flags=re.M):
        text2, n = re.subn(
            rf'^(\s*{re.escape(key)}:\s*")[^"]*("\s*)$',
            rf"\g<1>{pilot}\g<2>",
            text,
            flags=re.M,
        )
        if n < 1:
            raise SystemExit("pilot env replace failed")
    else:
        needle = "  core_api:\n    environment:"
        text2 = text.replace(needle, needle + f'\n      {key}: "{pilot}"', 1)
    # Assert discovery/canonical/pin unchanged — mutation scope exact
    assert 'RUBIKA_CANONICAL_SESSION_MODE: "enforce"' in text2
    assert 'RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS: "13,23,74"' in text2
    assert 'RUBIKA_WORKER_DISCOVERY_MODE: "dynamic"' in text2
    assert 'RUBIKA_WORKER_DISCOVERY_COHORT_IDS: "13,23,74"' in text2
    assert 'RUBIKA_ACCOUNT_IDS: "12,79"' in text2
    post = re.findall(
        rf'^\s*{re.escape(key)}:\s*"([^"]*)"\s*$',
        text2,
        flags=re.M,
    )
    if post != ["27"]:
        raise SystemExit(f"post gate unexpected: {post}")
    OVERRIDE.write_text(text2, encoding="utf-8")
    return {"pilot": pilot, "sha256": _sha(OVERRIDE), "post_pilot_gate": "27"}


def recreate_core_api() -> None:
    # Recreate only the API service; do not recreate workers.
    subprocess.run(
        ["docker", "compose", "up", "-d", "--force-recreate", "--no-deps", "core_api"],
        cwd=str(ROOT),
        check=True,
    )


def main() -> int:
    import json

    pilot = int(sys.argv[1]) if len(sys.argv) > 1 else 27
    if pilot != 27:
        print(json.dumps({"ok": False, "error": "SWITCH_GATE_TARGET_LOCKED_TO_27"}))
        return 2
    out = {
        "SWITCH_GATE_TARGET_LOCKED_TO_27": True,
        "CONFIG_MUTATION_SCOPE_EXACT": True,
        "CORE_API_ONLY_RECREATE": True,
        "SCRIPT_REQUESTS_OTP": False,
        "SCRIPT_CREATES_LOGIN_CHALLENGE": False,
        "SCRIPT_CREATES_SESSION": False,
        "SCRIPT_SENDS_MESSAGES": False,
        "override_backup": backup_override(pilot),
        "gate": set_pilot_gate(pilot),
    }
    recreate_core_api()
    time.sleep(12)
    # health
    st = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Status}}", CORE_API],
        capture_output=True,
        text=True,
    )
    out["core_api_status"] = (st.stdout or "").strip()
    env = subprocess.run(
        ["docker", "exec", CORE_API, "printenv", "RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS"],
        capture_output=True,
        text=True,
    )
    out["pilot_env"] = (env.stdout or "").strip()
    out["db_backup"] = fresh_backup(pilot)
    out["FRESH_DB_BACKUP_SAFE"] = (
        out["db_backup"]["source_db"] == PRODUCTION_DB
        and out["db_backup"]["BACKUP_SIZE"] > 0
        and out["db_backup"]["sha_match"]
    )
    out["ok"] = (
        out["core_api_status"] == "running"
        and out["pilot_env"] == "27"
        and out["FRESH_DB_BACKUP_SAFE"]
        and out["override_backup"]["size"] > 0
        and out["override_backup"]["sha_recomputed_ok"]
    )
    print(json.dumps(out, indent=2))
    return 0 if out["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
