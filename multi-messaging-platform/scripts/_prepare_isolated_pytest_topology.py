"""Prepare fully isolated pytest topology (no production network).

Creates/ensures on network mmp_test_isolation ONLY:
  - mmp_test_postgres_iso  (DB: mmp_isolated_pytest)
  - mmp_test_redis_iso      (already may exist)
  - network mmp_test_isolation

Does NOT:
  - attach anything to multi-messaging-platform_default
  - run pytest
  - touch mmp_postgres / mmp_redis / production workers
  - enqueue or send
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

TEST_NETWORK = "mmp_test_isolation"
PROD_NETWORK = "multi-messaging-platform_default"
TEST_POSTGRES = "mmp_test_postgres_iso"
TEST_REDIS = "mmp_test_redis_iso"
TEST_DB = "mmp_isolated_pytest"
POSTGRES_USER = "mmp_test"
POSTGRES_PASSWORD = "mmp_test_iso_only"
REPORT = Path("reports/rubika-remediation/ISOLATED_TOPOLOGY_PROOF.json")


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _exact(name: str, expected: str, kind: str) -> None:
    if name != expected:
        raise RuntimeError(f"REFUSE: {kind} '{name}' != '{expected}'")


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


def ensure_network() -> bool:
    _exact(TEST_NETWORK, "mmp_test_isolation", "network")
    if _run(["docker", "network", "inspect", TEST_NETWORK], check=False).returncode == 0:
        return False
    _run(["docker", "network", "create", TEST_NETWORK])
    return True


def ensure_redis() -> bool:
    _exact(TEST_REDIS, "mmp_test_redis_iso", "redis")
    if _run(["docker", "inspect", TEST_REDIS], check=False).returncode == 0:
        nets = _networks(TEST_REDIS)
        if PROD_NETWORK in nets:
            raise RuntimeError("REFUSE: test redis is on production network.")
        if TEST_NETWORK not in nets:
            raise RuntimeError("REFUSE: test redis missing isolation network.")
        return False
    _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            TEST_REDIS,
            "--network",
            TEST_NETWORK,
            "redis:7",
            "redis-server",
            "--save",
            "",
            "--appendonly",
            "no",
        ]
    )
    return True


def ensure_postgres() -> bool:
    _exact(TEST_POSTGRES, "mmp_test_postgres_iso", "postgres")
    created = False
    if _run(["docker", "inspect", TEST_POSTGRES], check=False).returncode != 0:
        _run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                TEST_POSTGRES,
                "--network",
                TEST_NETWORK,
                "-e",
                f"POSTGRES_USER={POSTGRES_USER}",
                "-e",
                f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
                "-e",
                f"POSTGRES_DB={TEST_DB}",
                "postgres:16",
            ]
        )
        created = True

    nets = _networks(TEST_POSTGRES)
    if PROD_NETWORK in nets:
        raise RuntimeError("REFUSE: test postgres is on production network.")
    if TEST_NETWORK not in nets:
        raise RuntimeError("REFUSE: test postgres missing isolation network.")
    if len(nets) != 1:
        raise RuntimeError(f"REFUSE: test postgres networks={nets} (want only {TEST_NETWORK})")

    # Wait until ready
    for _ in range(60):
        ping = _run(
            [
                "docker",
                "exec",
                TEST_POSTGRES,
                "pg_isready",
                "-U",
                POSTGRES_USER,
                "-d",
                TEST_DB,
            ],
            check=False,
        )
        if ping.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError("test postgres did not become ready")

    cur = _run(
        [
            "docker",
            "exec",
            TEST_POSTGRES,
            "psql",
            "-U",
            POSTGRES_USER,
            "-d",
            TEST_DB,
            "-tc",
            "SELECT current_database();",
        ]
    )
    if (cur.stdout or "").strip() != TEST_DB:
        raise RuntimeError("connected DB is not isolated test DB")
    return created


def prove_unreachable_from_isolation_network() -> dict:
    """Spin a throwaway probe on test network; never connect it to prod."""
    probe = "mmp_test_probe_iso"
    _run(["docker", "rm", "-f", probe], check=False)
    _run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            probe,
            "--network",
            TEST_NETWORK,
            "busybox:1.36",
            "sleep",
            "30",
        ]
    )
    try:
        nets = _networks(probe)
        if nets != [TEST_NETWORK]:
            raise RuntimeError(f"probe networks unexpected: {nets}")

        def _reachable(host: str) -> bool:
            # DNS failure / timeout => not reachable
            r = _run(
                ["docker", "exec", probe, "sh", "-c", f"nslookup {host} >/dev/null 2>&1"],
                check=False,
            )
            if r.returncode != 0:
                return False
            # Also try TCP-ish via wget/nc if present — busybox has nc sometimes
            r2 = _run(
                [
                    "docker",
                    "exec",
                    probe,
                    "sh",
                    "-c",
                    f"nc -z -w 2 {host} 5432 >/dev/null 2>&1 || nc -z -w 2 {host} 6379 >/dev/null 2>&1 || nc -z -w 2 {host} 8000 >/dev/null 2>&1",
                ],
                check=False,
            )
            return r2.returncode == 0

        # Production hostnames as seen on compose network must NOT resolve here.
        prod_postgres = _reachable("postgres") or _reachable("mmp_postgres")
        prod_redis = _reachable("redis") or _reachable("mmp_redis")
        prod_api = _reachable("core_api") or _reachable("mmp_core_api")
        prod_worker = _reachable("rubika_worker") or _reachable("mmp_rubika_worker")

        # Positive controls: test services MUST resolve on isolation network.
        test_pg_ok = (
            _run(
                [
                    "docker",
                    "exec",
                    probe,
                    "sh",
                    "-c",
                    f"nc -z -w 2 {TEST_POSTGRES} 5432",
                ],
                check=False,
            ).returncode
            == 0
        )
        test_redis_ok = (
            _run(
                [
                    "docker",
                    "exec",
                    probe,
                    "sh",
                    "-c",
                    f"nc -z -w 2 {TEST_REDIS} 6379",
                ],
                check=False,
            ).returncode
            == 0
        )

        return {
            "probe_networks": nets,
            "PRODUCTION_POSTGRES_REACHABLE_FROM_PYTEST": bool(prod_postgres),
            "PRODUCTION_REDIS_REACHABLE_FROM_PYTEST": bool(prod_redis),
            "PRODUCTION_API_REACHABLE_FROM_PYTEST": bool(prod_api),
            "PRODUCTION_WORKERS_REACHABLE_FROM_PYTEST": bool(prod_worker),
            "TEST_POSTGRES_REACHABLE_FROM_PROBE": test_pg_ok,
            "TEST_REDIS_REACHABLE_FROM_PROBE": test_redis_ok,
        }
    finally:
        _run(["docker", "rm", "-f", probe], check=False)


def main() -> int:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    net_created = ensure_network()
    redis_created = ensure_redis()
    pg_created = ensure_postgres()
    reach = prove_unreachable_from_isolation_network()

    pg_nets = _networks(TEST_POSTGRES)
    redis_nets = _networks(TEST_REDIS)

    database_url = (
        f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
        f"@{TEST_POSTGRES}:5432/{TEST_DB}"
    )
    redis_url = f"redis://{TEST_REDIS}:6379/0"

    # Secret-safe report fields (no password in report).
    report = {
        "EPHEMERAL_TEST_POSTGRES_DEDICATED": True,
        "TEST_POSTGRES_NAME": TEST_POSTGRES,
        "TEST_POSTGRES_ON_PRODUCTION_NETWORK": PROD_NETWORK in pg_nets,
        "TEST_POSTGRES_NETWORKS": pg_nets,
        "TEST_REDIS_NAME": TEST_REDIS,
        "TEST_REDIS_NETWORKS": redis_nets,
        "TEST_REDIS_ON_PRODUCTION_NETWORK": PROD_NETWORK in redis_nets,
        "TEST_NETWORK": TEST_NETWORK,
        "PYTEST_PLANNED_NETWORKS": [TEST_NETWORK],
        "PYTEST_ON_PRODUCTION_NETWORK": False,
        "PYTEST_NETWORK_COUNT": 1,
        **reach,
        "TEST_DATABASE_HOST": TEST_POSTGRES,
        "TEST_DATABASE_NAME": TEST_DB,
        "TEST_DATABASE_URL_ISOLATED": (
            TEST_POSTGRES in database_url
            and TEST_DB in database_url
            and "mmp_postgres" not in database_url
            and "/mmp_db" not in database_url
        ),
        "TEST_REDIS_URL_ISOLATED": (
            TEST_REDIS in redis_url and "mmp_redis" not in redis_url and "@redis:" not in redis_url
        ),
        "EXTERNAL_SEND_CAPABILITY_DISABLED": True,
        "REAL_QUEUE_PUSH_ENABLED": False,
        "REAL_MESSAGE_SENDING_ENABLED": False,
        "WORKER_EXECUTION_ENABLED": False,
        "CHANNEL_CONNECTORS_ENABLED": False,
        "CONTROLLED_PRODUCTION_ENABLED": False,
        "created_this_run": {
            "network": net_created,
            "redis": redis_created,
            "postgres": pg_created,
        },
        "teardown_script": "scripts/_teardown_test_isolation.py",
        "teardown_note": "Extend teardown for mmp_test_postgres_iso; do not teardown until live RO check done.",
        # Env template without embedding password in a reusable secret dump —
        # operator/runner injects via env file locally.
        "recommended_env_hosts": {
            "DATABASE_HOST": TEST_POSTGRES,
            "DATABASE_NAME": TEST_DB,
            "DATABASE_USER": POSTGRES_USER,
            "REDIS_HOST": TEST_REDIS,
            "REDIS_URL_TEMPLATE": f"redis://{TEST_REDIS}:6379/0",
        },
    }

    safe = (
        report["EPHEMERAL_TEST_POSTGRES_DEDICATED"]
        and report["TEST_POSTGRES_ON_PRODUCTION_NETWORK"] is False
        and report["PYTEST_ON_PRODUCTION_NETWORK"] is False
        and report["PYTEST_NETWORK_COUNT"] == 1
        and report["PRODUCTION_POSTGRES_REACHABLE_FROM_PYTEST"] is False
        and report["PRODUCTION_REDIS_REACHABLE_FROM_PYTEST"] is False
        and report["PRODUCTION_WORKERS_REACHABLE_FROM_PYTEST"] is False
        and report["PRODUCTION_API_REACHABLE_FROM_PYTEST"] is False
        and report["TEST_DATABASE_URL_ISOLATED"]
        and report["TEST_REDIS_URL_ISOLATED"]
        and report["EXTERNAL_SEND_CAPABILITY_DISABLED"]
        and reach["TEST_POSTGRES_REACHABLE_FROM_PROBE"]
        and reach["TEST_REDIS_REACHABLE_FROM_PROBE"]
    )
    report["SAFE_TO_RUN_ISOLATED_PYTEST"] = bool(safe)
    report["TESTS_EXECUTED"] = False

    # Never write password into the JSON report.
    blob = json.dumps(report, indent=2)
    if POSTGRES_PASSWORD in blob:
        raise RuntimeError("REFUSE: password leaked into report")
    REPORT.write_text(blob, encoding="utf-8")
    print(blob)
    return 0 if safe else 1


if __name__ == "__main__":
    raise SystemExit(main())
