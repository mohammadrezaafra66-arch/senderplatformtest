#!/usr/bin/env python3
"""L16 Account27 rollout: cohort expand -> worker verify -> enforce expand.

Host-side config mutation only (docker-compose.override.yml) + selective recreate.
Never OTP, never send, never session row edits.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERRIDE = ROOT / "docker-compose.override.yml"
REPORTS = ROOT / "reports" / "rubika-remediation"
EXPECTED_AFTER_COHORT = [12, 13, 23, 27, 74, 79]
EXPECTED_ROLLBACK_WORKERS = [12, 13, 23, 74, 79]
BATCH = {13: 729, 23: 725, 74: 724}
PILOT = 27
SID = 772


def _vals(text: str, key: str) -> list[str]:
    return re.findall(rf'^\s*{re.escape(key)}:\s*"([^"]*)"\s*$', text, flags=re.M)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup(tag: str) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rb = ROOT / f"docker-compose.override.yml.before-l16-{tag}-{ts}.bak"
    shutil.copy2(OVERRIDE, rb)
    t = rb.read_text(encoding="utf-8")
    meta = {
        "rollback_file": str(rb),
        "sha256": _sha(rb),
        "size": rb.stat().st_size,
        "mode": _vals(t, "RUBIKA_CANONICAL_SESSION_MODE"),
        "allow": _vals(t, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS"),
        "discovery": _vals(t, "RUBIKA_WORKER_DISCOVERY_MODE"),
        "cohort": _vals(t, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS"),
        "pin": _vals(t, "RUBIKA_ACCOUNT_IDS"),
        "login_pilot": _vals(t, "RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS"),
    }
    (REPORTS / f"_l16_{tag}_rollback_meta.txt").write_text(
        "\n".join(f"{k}={v}" for k, v in meta.items()) + "\n", encoding="utf-8"
    )
    return meta


def set_key(key: str, value: str) -> dict:
    text = OVERRIDE.read_text(encoding="utf-8")
    text2, n = re.subn(
        rf'^(\s*{re.escape(key)}:\s*")[^"]*("\s*)$',
        rf"\g<1>{value}\g<2>",
        text,
        flags=re.M,
    )
    if n < 1:
        raise SystemExit(f"replace failed key={key} n={n}")
    OVERRIDE.write_text(text2, encoding="utf-8")
    return {"key": key, "value": value, "replacements": n, "sha256": _sha(OVERRIDE)}


def restore(path: Path) -> dict:
    shutil.copy2(path, OVERRIDE)
    return {"restored_from": str(path), "sha256": _sha(OVERRIDE)}


def compose_recreate(*services: str) -> dict:
    cmd = [
        "docker",
        "compose",
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
        *services,
    ]
    p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    return {
        "cmd": cmd,
        "rc": p.returncode,
        "stdout_tail": (p.stdout or "")[-500:],
        "stderr_tail": (p.stderr or "")[-500:],
    }


def worker_probe() -> dict:
    code = (
        "import json;"
        "from workers.config import get_worker_settings;"
        "from workers.rubika_pool_worker import resolve_rubika_worker_account_ids_from_settings;"
        "s=get_worker_settings(); ids,meta=resolve_rubika_worker_account_ids_from_settings(s);"
        "print(json.dumps({"
        "'mode':meta.get('mode'),"
        "'cohort':meta.get('cohort_ids'),"
        "'pin':meta.get('pinned_ids'),"
        "'actual':ids,"
        "'canon_mode':s.RUBIKA_CANONICAL_SESSION_MODE,"
        "'canon_allow':s.RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS"
        "}))"
    )
    p = subprocess.run(
        ["docker", "exec", "-e", "PYTHONPATH=/app", "-w", "/app", "mmp_rubika_worker", "python", "-c", code],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        return {"ok": False, "stderr": (p.stderr or "")[-400:]}
    return {"ok": True, **json.loads(p.stdout.strip().splitlines()[-1])}


def observe_workers(n: int = 3, sleep_s: float = 65.0) -> dict:
    """Observe resolved workers across n cycles (refresh interval is 60s)."""
    obs = []
    for i in range(n):
        probe = worker_probe()
        cov_code = (
            "import asyncio,json,redis.asyncio as redis;"
            "from workers.config import get_worker_settings;"
            "from workers.redis_keys import worker_account_coverage_key;"
            "s=get_worker_settings();"
            "async def r():\n"
            "  c=redis.from_url(s.REDIS_URL,decode_responses=True); await c.ping();\n"
            "  out={};\n"
            "  for a in (12,13,23,27,74,79):\n"
            "    out[str(a)]={'cov':bool(await c.exists(worker_account_coverage_key('rubika',a))),"
            "'q':int(await c.llen(f'queue:rubika:{a}'))};\n"
            "  await c.aclose(); print(json.dumps(out))\n"
            "asyncio.run(r())"
        )
        # simpler multiline via python -c is painful; use compact
        cov_code = (
            "import asyncio,json,redis.asyncio as redis;"
            "from workers.config import get_worker_settings;"
            "from workers.redis_keys import worker_account_coverage_key as k;"
            "s=get_worker_settings();\n"
            "async def main():\n"
            " r=redis.from_url(s.REDIS_URL,decode_responses=True); await r.ping(); o={};\n"
            " ids=[12,13,23,27,74,79]\n"
            " for a in ids: o[str(a)]={'cov':bool(await r.exists(k('rubika',a))),'q':int(await r.llen(f'queue:rubika:{a}'))}\n"
            " await r.aclose(); print(json.dumps(o))\n"
            "asyncio.run(main())"
        )
        cp = subprocess.run(
            ["docker", "exec", "-e", "PYTHONPATH=/app", "-w", "/app", "mmp_rubika_worker", "python", "-c", cov_code],
            capture_output=True,
            text=True,
        )
        cov = json.loads(cp.stdout.strip().splitlines()[-1]) if cp.returncode == 0 else {"error": cp.stderr[-200:]}
        obs.append({"i": i + 1, "probe": probe, "coverage": cov})
        if i < n - 1:
            time.sleep(sleep_s)
    stable = all(
        o["probe"].get("ok") and o["probe"].get("actual") == EXPECTED_AFTER_COHORT for o in obs
    )
    health = all(
        isinstance(o["coverage"], dict)
        and o["coverage"].get("27", {}).get("cov") is True
        and o["coverage"].get("27", {}).get("q") == 0
        and all(o["coverage"].get(str(a), {}).get("cov") is True for a in (12, 13, 23, 74, 79))
        for o in obs
    )
    return {
        "observations": obs,
        "PILOT_WORKER_PRESENT": stable,
        "PILOT_WORKER_HEALTH_PASS": health,
        "PILOT_WORKER_COVERAGE_STABLE": stable and health,
        "ACTUAL_WORKER_IDS": obs[-1]["probe"].get("actual") if obs else None,
    }


def enforce_verify() -> dict:
    code = Path(ROOT / "scripts" / "_l16_a27_enforce_verify.py").read_text(encoding="utf-8")
    # run via core_api bind mount
    p = subprocess.run(
        [
            "docker",
            "exec",
            "-e",
            "PYTHONPATH=/app",
            "-w",
            "/app",
            "mmp_core_api",
            "python",
            "scripts/_l16_a27_enforce_verify.py",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    out = {"rc": p.returncode, "stdout_tail": (p.stdout or "")[-2000:], "stderr_tail": (p.stderr or "")[-500:]}
    art_path = ROOT / "scripts" / "_l16_a27_enforce_verify.out.json"
    if art_path.is_file():
        out["artifact"] = json.loads(art_path.read_text(encoding="utf-8"))
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: backup-cohort | set-cohort | recreate-worker | observe | "
            "rollback-cohort PATH | backup-enforce | set-enforce | recreate-consumers | "
            "rollback-enforce PATH | worker-probe"
        )
        return 2
    cmd = sys.argv[1]
    if cmd == "backup-cohort":
        # require current cohort 13,23,74 — exact pre-change rollback provenance
        t = OVERRIDE.read_text(encoding="utf-8")
        assert _vals(t, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == ["13,23,74"], _vals(
            t, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS"
        )
        assert _vals(t, "RUBIKA_WORKER_DISCOVERY_MODE") == ["dynamic"]
        assert _vals(t, "RUBIKA_ACCOUNT_IDS") == ["12,79"]
        assert set(_vals(t, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS")) == {"13,23,74"}
        assert set(_vals(t, "RUBIKA_CANONICAL_SESSION_MODE")) == {"enforce"}
        meta = backup("a27-cohort")
        rb = Path(meta["rollback_file"])
        assert rb.is_file() and rb.stat().st_size > 0
        assert meta["sha256"] == _sha(rb)
        assert meta["discovery"] == ["dynamic"]
        assert meta["cohort"] == ["13,23,74"]
        assert meta["pin"] == ["12,79"]
        assert set(meta["mode"]) == {"enforce"}
        assert set(meta["allow"]) == {"13,23,74"}
        meta["rollback_hard_gate_pass"] = True
        print(json.dumps(meta, indent=2))
        return 0
    if cmd == "set-cohort":
        # ONLY cohort key: 13,23,74 -> 13,23,27,74. Canonical/pin/mode untouched.
        before = OVERRIDE.read_text(encoding="utf-8")
        assert _vals(before, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == ["13,23,74"], _vals(
            before, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS"
        )
        assert _vals(before, "RUBIKA_WORKER_DISCOVERY_MODE") == ["dynamic"]
        assert _vals(before, "RUBIKA_ACCOUNT_IDS") == ["12,79"]
        assert set(_vals(before, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS")) == {"13,23,74"}
        assert set(_vals(before, "RUBIKA_CANONICAL_SESSION_MODE")) == {"enforce"}
        r = set_key("RUBIKA_WORKER_DISCOVERY_COHORT_IDS", "13,23,27,74")
        after = OVERRIDE.read_text(encoding="utf-8")
        assert _vals(after, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == ["13,23,27,74"]
        assert _vals(after, "RUBIKA_ACCOUNT_IDS") == ["12,79"]
        assert set(_vals(after, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS")) == {"13,23,74"}
        assert set(_vals(after, "RUBIKA_CANONICAL_SESSION_MODE")) == {"enforce"}
        assert _vals(after, "RUBIKA_WORKER_DISCOVERY_MODE") == ["dynamic"]
        assert _vals(after, "RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS") == _vals(
            before, "RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS"
        )
        print(json.dumps(r, indent=2))
        return 0
    if cmd == "recreate-worker":
        # rubika_worker only; no --build; no core_api
        out = compose_recreate("rubika_worker")
        assert out["cmd"] == [
            "docker",
            "compose",
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "rubika_worker",
        ], out["cmd"]
        assert "--build" not in out["cmd"]
        assert "core_api" not in out["cmd"]
        print(json.dumps(out, indent=2))
        return 0
    if cmd == "observe":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        sleep_s = float(sys.argv[3]) if len(sys.argv) > 3 else 65.0
        art = observe_workers(n=n, sleep_s=sleep_s)
        (REPORTS / "L16_A27_WORKER_OBSERVE.json").write_text(
            json.dumps(art, indent=2), encoding="utf-8"
        )
        print(json.dumps(art, indent=2))
        return 0 if art["PILOT_WORKER_COVERAGE_STABLE"] else 2
    if cmd == "rollback-cohort":
        path = Path(sys.argv[2])
        print(json.dumps(restore(path), indent=2))
        print(json.dumps(compose_recreate("rubika_worker"), indent=2))
        return 0
    if cmd == "backup-enforce":
        # Pre-ENFORCE rollback: allowlist still 13,23,74; cohort already 13,23,27,74
        t = OVERRIDE.read_text(encoding="utf-8")
        assert _vals(t, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == ["13,23,27,74"], _vals(
            t, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS"
        )
        assert _vals(t, "RUBIKA_WORKER_DISCOVERY_MODE") == ["dynamic"]
        assert _vals(t, "RUBIKA_ACCOUNT_IDS") == ["12,79"]
        assert set(_vals(t, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS")) == {"13,23,74"}
        assert set(_vals(t, "RUBIKA_CANONICAL_SESSION_MODE")) == {"enforce"}
        meta = backup("a27-enforce")
        rb = Path(meta["rollback_file"])
        assert rb.is_file() and rb.stat().st_size > 0
        assert meta["sha256"] == _sha(rb)
        assert meta["discovery"] == ["dynamic"]
        assert meta["cohort"] == ["13,23,27,74"]
        assert meta["pin"] == ["12,79"]
        assert set(meta["mode"]) == {"enforce"}
        assert set(meta["allow"]) == {"13,23,74"}
        meta["rollback_hard_gate_pass"] = True
        meta["note"] = "rollback restores allowlist only path; does not touch Session772/OTP"
        print(json.dumps(meta, indent=2))
        return 0
    if cmd == "set-enforce":
        # ONLY allowlist: 13,23,74 -> 13,23,27,74. Worker cohort/pin/mode untouched.
        before = OVERRIDE.read_text(encoding="utf-8")
        assert _vals(before, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == ["13,23,27,74"]
        assert _vals(before, "RUBIKA_WORKER_DISCOVERY_MODE") == ["dynamic"]
        assert _vals(before, "RUBIKA_ACCOUNT_IDS") == ["12,79"]
        assert set(_vals(before, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS")) == {"13,23,74"}
        assert set(_vals(before, "RUBIKA_CANONICAL_SESSION_MODE")) == {"enforce"}
        r = set_key("RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", "13,23,27,74")
        after = OVERRIDE.read_text(encoding="utf-8")
        assert set(_vals(after, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS")) == {"13,23,27,74"}
        assert set(_vals(after, "RUBIKA_CANONICAL_SESSION_MODE")) == {"enforce"}
        assert _vals(after, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS") == ["13,23,27,74"]
        assert _vals(after, "RUBIKA_WORKER_DISCOVERY_MODE") == ["dynamic"]
        assert _vals(after, "RUBIKA_ACCOUNT_IDS") == ["12,79"]
        assert _vals(after, "RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS") == _vals(
            before, "RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS"
        )
        print(json.dumps(r, indent=2))
        return 0
    if cmd == "recreate-consumers":
        # Proven canonical consumers: core_api + rubika_worker; no --build; no postgres/redis
        out = compose_recreate("core_api", "rubika_worker")
        assert out["cmd"] == [
            "docker",
            "compose",
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "core_api",
            "rubika_worker",
        ], out["cmd"]
        assert "--build" not in out["cmd"]
        assert "postgres" not in out["cmd"]
        assert "redis" not in out["cmd"]
        assert "celery" not in "".join(out["cmd"])
        print(json.dumps(out, indent=2))
        return 0
    if cmd == "rollback-enforce":
        path = Path(sys.argv[2])
        print(json.dumps(restore(path), indent=2))
        # restore allowlist consumers only; cohort remains whatever is in rollback file
        # (pre-enforce bak has cohort 13,23,27,74 — correct)
        out = compose_recreate("core_api", "rubika_worker")
        print(json.dumps(out, indent=2))
        return 0
    if cmd == "worker-probe":
        print(json.dumps(worker_probe(), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
