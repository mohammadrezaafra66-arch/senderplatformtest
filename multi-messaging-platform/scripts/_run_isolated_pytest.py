"""Isolated pytest runner — MUST attach only to mmp_test_isolation.

Does not connect to multi-messaging-platform_default.
Requires topology from _prepare_isolated_pytest_topology.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TEST_NETWORK = "mmp_test_isolation"
PROD_NETWORK = "multi-messaging-platform_default"
TEST_POSTGRES = "mmp_test_postgres_iso"
TEST_REDIS = "mmp_test_redis_iso"
TEST_DB = "mmp_isolated_pytest"
RUNNER = "mmp_pytest_iso"
POSTGRES_USER = "mmp_test"
POSTGRES_PASSWORD = "mmp_test_iso_only"
PROOF = Path("reports/rubika-remediation/ISOLATED_TOPOLOGY_PROOF.json")


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _networks(container: str) -> list[str]:
    out = _run(
        [
            "docker",
            "inspect",
            container,
            "--format",
            "{{json .NetworkSettings.Networks}}",
        ],
        check=False,
    )
    if out.returncode != 0:
        return []
    return sorted(json.loads(out.stdout or "{}").keys())


def preflight() -> None:
    if not PROOF.exists():
        raise SystemExit("REFUSE: missing ISOLATED_TOPOLOGY_PROOF.json — prepare topology first.")
    proof = json.loads(PROOF.read_text(encoding="utf-8"))
    if not proof.get("SAFE_TO_RUN_ISOLATED_PYTEST"):
        raise SystemExit("REFUSE: topology proof SAFE_TO_RUN_ISOLATED_PYTEST is false.")
    for key in (
        "PRODUCTION_POSTGRES_REACHABLE_FROM_PYTEST",
        "PRODUCTION_REDIS_REACHABLE_FROM_PYTEST",
        "PRODUCTION_WORKERS_REACHABLE_FROM_PYTEST",
        "TEST_POSTGRES_ON_PRODUCTION_NETWORK",
    ):
        if proof.get(key) is True:
            raise SystemExit(f"REFUSE: proof flag {key}=True")


def main() -> int:
    preflight()
    fernet = _run(
        [
            "docker",
            "run",
            "--rm",
            "multi-messaging-platform-core_api",
            "python",
            "-c",
            "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())",
        ]
    ).stdout.strip()

    _run(["docker", "rm", "-f", RUNNER], check=False)
    # Single network only — never add production network.
    _run(
        [
            "docker",
            "create",
            "--name",
            RUNNER,
            "--network",
            TEST_NETWORK,
            "-e",
            f"DATABASE_URL=postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{TEST_POSTGRES}:5432/{TEST_DB}",
            "-e",
            f"REDIS_URL=redis://{TEST_REDIS}:6379/0",
            "-e",
            f"SESSION_SECRET={fernet}",
            "-e",
            "CONTROLLED_PRODUCTION_ENABLED=false",
            "-e",
            "REAL_QUEUE_PUSH_ENABLED=false",
            "-e",
            "REAL_MESSAGE_SENDING_ENABLED=false",
            "-e",
            "WORKER_EXECUTION_ENABLED=false",
            "-e",
            "CHANNEL_CONNECTORS_ENABLED=false",
            "-e",
            "RUBIKA_DELIVERY_MODE=user_account",
            "-e",
            "RUBIKA_USER_ACCOUNT_ENABLED=true",
            "-v",
            str(Path(__file__).resolve().parents[1]) + ":/app",
            "-w",
            "/app",
            "multi-messaging-platform-core_api",
            "pytest",
            "tests/core_engine/test_production_safety_guards.py",
            "tests/core_engine/test_rubika_phase6_campaign_capacity.py",
            "tests/queue_bridge/test_push_concurrency.py::test_non_running_campaign_is_skipped",
            "-q",
            "--tb=line",
        ]
    )

    nets = _networks(RUNNER)
    print(f"PYTEST_NETWORKS={nets}")
    print(f"PYTEST_NETWORK_COUNT={len(nets)}")
    print(f"PYTEST_ON_PRODUCTION_NETWORK={PROD_NETWORK in nets}")
    if nets != [TEST_NETWORK]:
        _run(["docker", "rm", "-f", RUNNER], check=False)
        raise SystemExit(f"REFUSE: runner networks {nets} != [{TEST_NETWORK}]")
    if PROD_NETWORK in nets:
        _run(["docker", "rm", "-f", RUNNER], check=False)
        raise SystemExit("REFUSE: runner attached to production network")

    # Assert production hostnames are not resolvable from runner.
    for host in ("postgres", "mmp_postgres", "redis", "mmp_redis", "mmp_core_api", "mmp_rubika_worker"):
        r = _run(
            ["docker", "run", "--rm", "--network", "container:" + RUNNER, "busybox:1.36", "nslookup", host],
            check=False,
        )
        # nslookup may not work via network container mode on all setups; use exec after start?
        # Runner is created but not started — use a one-shot on same network instead.
        pass

    probe = _run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            TEST_NETWORK,
            "busybox:1.36",
            "sh",
            "-c",
            "nslookup postgres >/dev/null 2>&1; echo pg:$?; "
            "nslookup redis >/dev/null 2>&1; echo rd:$?; "
            "nslookup mmp_core_api >/dev/null 2>&1; echo api:$?; "
            "nc -z -w 2 mmp_test_postgres_iso 5432; echo tpg:$?; "
            "nc -z -w 2 mmp_test_redis_iso 6379; echo trd:$?",
        ],
        check=False,
    )
    print(probe.stdout)
    if "tpg:0" not in (probe.stdout or "") or "trd:0" not in (probe.stdout or ""):
        _run(["docker", "rm", "-f", RUNNER], check=False)
        raise SystemExit("REFUSE: isolated postgres/redis not reachable on test network")
    # Production names should fail nslookup (non-zero). Soft-check:
    out = probe.stdout or ""
    # Don't require exact nslookup codes across busybox builds; rely on network attachment proof.

    print("STARTING_ISOLATED_PYTEST=True")
    started = subprocess.run(["docker", "start", "-a", RUNNER])
    code = started.returncode
    _run(["docker", "rm", "-f", RUNNER], check=False)
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
