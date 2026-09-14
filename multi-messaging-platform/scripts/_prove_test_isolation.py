"""Prove hermetic test DB/Redis isolation WITHOUT running pytest suites.

Allowed mutations (exact names only):
  - CREATE DATABASE mmp_isolated_pytest (never mmp_db)
  - create Docker network mmp_test_isolation (if missing)
  - create container mmp_test_redis_iso on that network
  - write local report ISOLATION_PROOF.json (no credential DSNs)

Does NOT:
  - run pytest
  - select or mutate mmp_db application data
  - connect to production Redis
  - start/restart workers
  - enqueue or send messages
  - tear down resources (deferred — see _teardown_test_isolation.py)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

PRODUCTION_DB_NAME = "mmp_db"
TEST_DB_NAME = "mmp_isolated_pytest"
TEST_REDIS_NAME = "mmp_test_redis_iso"
TEST_NETWORK = "mmp_test_isolation"
PROD_NETWORK = "multi-messaging-platform_default"
REPORT_PATH = Path("reports/rubika-remediation/ISOLATION_PROOF.json")

# Explicit allowed mutation inventory for this proof script.
ALLOWED_MUTATIONS = (
    f"CREATE DATABASE {TEST_DB_NAME}",
    f"docker network create {TEST_NETWORK}",
    f"docker run --name {TEST_REDIS_NAME} --network {TEST_NETWORK}",
    f"write {REPORT_PATH.as_posix()}",
)

# Teardown is deferred until after isolated pytest; ownership documented here.
TEARDOWN_OWNERSHIP = {
    "deferred_until": "after_isolated_pytest_verification",
    "teardown_script": "scripts/_teardown_test_isolation.py",
    "exact_targets": {
        "database": TEST_DB_NAME,
        "redis_container": TEST_REDIS_NAME,
        "docker_network": TEST_NETWORK,
    },
    "never_targets": {
        "database": PRODUCTION_DB_NAME,
        "redis_container": "mmp_redis",
        "docker_network": PROD_NETWORK,
    },
}


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _assert_exact_name(actual: str, expected: str, *, kind: str) -> None:
    if actual != expected:
        raise RuntimeError(
            f"REFUSE: {kind} name '{actual}' does not match exact allowed '{expected}'."
        )


def _safe_redis_endpoint(url: str) -> dict:
    """Derive host/port/db only — never password, username, or full DSN."""
    if not url:
        return {
            "worker_redis_host": None,
            "worker_redis_port": None,
            "worker_redis_db_index": None,
        }
    # Strip credentials before parse so they never linger in structured output.
    scrubbed = re.sub(r"(redis://)([^@/]+)@", r"\1***@", url)
    parsed = urlparse(url)
    db = 0
    if parsed.path and parsed.path.strip("/"):
        try:
            db = int(parsed.path.strip("/").split("/")[0])
        except ValueError:
            db = 0
    return {
        "worker_redis_host": parsed.hostname,
        "worker_redis_port": int(parsed.port or 6379),
        "worker_redis_db_index": db,
        # Evidence that scrubbing ran; never store scrubbed URL either if it still
        # looks credential-bearing — omit URL fields entirely from report.
        "_credential_fields_omitted": True,
        "_scrub_applied": "***" in scrubbed or "@" not in url.split("://", 1)[-1].split("/", 1)[0],
    }


def ensure_test_db() -> dict:
    _assert_exact_name(TEST_DB_NAME, "mmp_isolated_pytest", kind="test_database")
    if TEST_DB_NAME == PRODUCTION_DB_NAME:
        raise RuntimeError("REFUSE: test DB name collides with production.")

    # Catalog ops on database 'postgres' only — never -d mmp_db.
    exists = _run(
        [
            "docker",
            "exec",
            "mmp_postgres",
            "psql",
            "-U",
            "mmp_user",
            "-d",
            "postgres",
            "-tc",
            f"SELECT 1 FROM pg_database WHERE datname='{TEST_DB_NAME}'",
        ]
    )
    created = False
    if "1" not in (exists.stdout or ""):
        _run(
            [
                "docker",
                "exec",
                "mmp_postgres",
                "psql",
                "-U",
                "mmp_user",
                "-d",
                "postgres",
                "-c",
                f"CREATE DATABASE {TEST_DB_NAME} OWNER mmp_user;",
            ]
        )
        created = True

    tables = _run(
        [
            "docker",
            "exec",
            "mmp_postgres",
            "psql",
            "-U",
            "mmp_user",
            "-d",
            TEST_DB_NAME,
            "-tc",
            "SELECT current_database();",
        ]
    )
    current = (tables.stdout or "").strip()
    visibility = False
    canary = _run(
        [
            "docker",
            "exec",
            "mmp_postgres",
            "psql",
            "-U",
            "mmp_user",
            "-d",
            TEST_DB_NAME,
            "-tc",
            """
            SELECT COALESCE(
              (SELECT count(*) FROM information_schema.tables
               WHERE table_schema='public' AND table_name='accounts'), 0);
            """,
        ],
        check=False,
    )
    raw = (canary.stdout or "").strip().splitlines()
    has_accounts = False
    if raw:
        try:
            has_accounts = int(raw[-1].strip() or "0") > 0
        except ValueError:
            has_accounts = False
    if has_accounts:
        cnt = _run(
            [
                "docker",
                "exec",
                "mmp_postgres",
                "psql",
                "-U",
                "mmp_user",
                "-d",
                TEST_DB_NAME,
                "-tc",
                "SELECT count(*) FROM accounts WHERE id IN (12,79);",
            ],
            check=False,
        )
        if cnt.returncode == 0:
            visibility = int((cnt.stdout or "0").strip().splitlines()[-1] or "0") > 0

    return {
        "TEST_DB_NAME": TEST_DB_NAME,
        "PRODUCTION_DB_NAME": PRODUCTION_DB_NAME,
        "TEST_DB_IS_PRODUCTION": current == PRODUCTION_DB_NAME,
        "PRODUCTION_ROW_VISIBILITY_FROM_TEST_DB": visibility,
        "test_db_created_this_run": created,
        "connected_database": current,
        "TEST_DB_TEARDOWN_DEFERRED": True,
        "TEST_DB_TEARDOWN_SCRIPT": TEARDOWN_OWNERSHIP["teardown_script"],
    }


def ensure_test_redis() -> dict:
    _assert_exact_name(TEST_NETWORK, "mmp_test_isolation", kind="test_network")
    _assert_exact_name(TEST_REDIS_NAME, "mmp_test_redis_iso", kind="test_redis_container")

    # Allowed network mutation: create ONLY mmp_test_isolation if missing.
    inspect_net = _run(["docker", "network", "inspect", TEST_NETWORK], check=False)
    network_created = False
    if inspect_net.returncode != 0:
        # Exact argv: only mmp_test_isolation may be created.
        _run(["docker", "network", "create", TEST_NETWORK])
        network_created = True

    inspect = _run(["docker", "inspect", TEST_REDIS_NAME], check=False)
    redis_created = False
    if inspect.returncode != 0:
        _run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                TEST_REDIS_NAME,
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
        redis_created = True

    nets = _run(
        [
            "docker",
            "inspect",
            TEST_REDIS_NAME,
            "--format",
            "{{json .NetworkSettings.Networks}}",
        ]
    )
    networks = json.loads(nets.stdout or "{}")
    on_prod_network = PROD_NETWORK in networks
    on_test_network = TEST_NETWORK in networks
    test_network_names = sorted(networks.keys())

    worker_env = _run(
        [
            "docker",
            "inspect",
            "mmp_rubika_worker",
            "--format",
            "{{range .Config.Env}}{{println .}}{{end}}",
        ]
    )
    worker_redis_raw = ""
    for line in (worker_env.stdout or "").splitlines():
        if line.startswith("REDIS_URL="):
            worker_redis_raw = line.split("=", 1)[1]
            break
    worker_endpoint = _safe_redis_endpoint(worker_redis_raw)
    # Drop any chance of retaining the raw URL.
    del worker_redis_raw

    worker_nets = _run(
        [
            "docker",
            "inspect",
            "mmp_rubika_worker",
            "--format",
            "{{json .NetworkSettings.Networks}}",
        ]
    )
    worker_networks = json.loads(worker_nets.stdout or "{}")
    worker_network_names = sorted(worker_networks.keys())
    worker_can_reach = TEST_NETWORK in worker_networks and on_test_network

    same_as_test_redis = (
        worker_endpoint.get("worker_redis_host") in {TEST_REDIS_NAME, "mmp_test_redis_iso"}
    )

    ping = _run(
        ["docker", "exec", TEST_REDIS_NAME, "redis-cli", "PING"],
        check=False,
    )

    return {
        "TEST_REDIS_NAME": TEST_REDIS_NAME,
        "TEST_NETWORK": TEST_NETWORK,
        "TEST_REDIS_ISOLATED": on_test_network and not on_prod_network,
        "TEST_REDIS_ON_PRODUCTION_NETWORK": on_prod_network,
        "test_redis_network_names": test_network_names,
        "worker_network_names": worker_network_names,
        **worker_endpoint,
        "same_as_test_redis": bool(same_as_test_redis),
        "PRODUCTION_WORKER_CAN_CONSUME_TEST_QUEUE": bool(worker_can_reach),
        "TEST_REDIS_PING": (ping.stdout or "").strip(),
        "REAL_QUEUE_PUSH_TO_PRODUCTION": False,
        "LIVE_WORKERS_USED_FOR_TESTS": False,
        "test_network_created_this_run": network_created,
        "test_redis_created_this_run": redis_created,
        "TEST_REDIS_TEARDOWN_DEFERRED": True,
        "TEST_NETWORK_TEARDOWN_DEFERRED": True,
        "TEARDOWN_SCRIPT": TEARDOWN_OWNERSHIP["teardown_script"],
        "ALLOWED_MUTATIONS": list(ALLOWED_MUTATIONS),
    }


def prove_fail_fast_helpers() -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tests.isolation import (
        assert_test_database_url,
        assert_test_redis_url,
        is_production_database_url,
    )

    refused_prod_db = False
    try:
        assert_test_database_url(
            f"postgresql://mmp_user@postgres:5432/{PRODUCTION_DB_NAME}"
        )
    except RuntimeError:
        refused_prod_db = True

    refused_prod_redis = False
    try:
        assert_test_redis_url("redis://redis:6379/0")
    except RuntimeError:
        refused_prod_redis = True

    accepted_test_db = False
    accepted_test_db_error = None
    try:
        assert_test_database_url(
            f"postgresql://mmp_user@postgres:5432/{TEST_DB_NAME}"
        )
        accepted_test_db = True
    except RuntimeError as exc:
        accepted_test_db_error = str(exc)

    accepted_test_redis = False
    accepted_test_redis_error = None
    try:
        assert_test_redis_url(f"redis://{TEST_REDIS_NAME}:6379/0")
        accepted_test_redis = True
    except RuntimeError as exc:
        accepted_test_redis_error = str(exc)

    return {
        "fail_fast_refuses_production_db": refused_prod_db,
        "fail_fast_refuses_production_redis": refused_prod_redis,
        "fail_fast_accepts_test_db": accepted_test_db,
        "fail_fast_accepts_test_redis": accepted_test_redis,
        "is_production_database_url_mmp_db": is_production_database_url(
            f"postgresql://x@h/{PRODUCTION_DB_NAME}"
        ),
        "accepted_test_db_error": accepted_test_db_error,
        "accepted_test_redis_error": accepted_test_redis_error,
    }


def main() -> int:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = ensure_test_db()
    redis = ensure_test_redis()
    helpers = prove_fail_fast_helpers()

    test_db_isolated = (
        db["TEST_DB_IS_PRODUCTION"] is False
        and db["PRODUCTION_ROW_VISIBILITY_FROM_TEST_DB"] is False
        and db["connected_database"] == TEST_DB_NAME
    )
    test_redis_isolated = bool(redis["TEST_REDIS_ISOLATED"]) and not bool(
        redis["PRODUCTION_WORKER_CAN_CONSUME_TEST_QUEUE"]
    )
    safe = (
        test_db_isolated
        and test_redis_isolated
        and helpers["fail_fast_refuses_production_db"]
        and helpers["fail_fast_refuses_production_redis"]
        and helpers["fail_fast_accepts_test_db"]
        and helpers["fail_fast_accepts_test_redis"]
        and redis["LIVE_WORKERS_USED_FOR_TESTS"] is False
        and redis["REAL_QUEUE_PUSH_TO_PRODUCTION"] is False
    )

    report = {
        **db,
        **redis,
        **helpers,
        "TEST_DB_ISOLATED": test_db_isolated,
        "SAFE_TO_RUN_ISOLATED_REGRESSION_TESTS": safe,
        "TESTS_EXECUTED": False,
        "TEARDOWN_OWNERSHIP": TEARDOWN_OWNERSHIP,
        "REPORT_SECRET_SAFE": True,
        "recommended_env": {
            # No passwords in recommended_env — operator supplies credentials via env.
            "DATABASE_URL": (
                f"postgresql://$MMP_TEST_DB_USER:$MMP_TEST_DB_PASSWORD"
                f"@postgres:5432/{TEST_DB_NAME}"
            ),
            "REDIS_URL": f"redis://{TEST_REDIS_NAME}:6379/0",
            "REAL_QUEUE_PUSH_ENABLED": "false",
            "REAL_MESSAGE_SENDING_ENABLED": "false",
            "WORKER_EXECUTION_ENABLED": "false",
            "CHANNEL_CONNECTORS_ENABLED": "false",
            "note": (
                "Test runner must attach to networks "
                f"'{TEST_NETWORK}' (for Redis) and '{PROD_NETWORK}' "
                "(for Postgres host 'postgres' only). "
                "Do not attach mmp_rubika_worker to the test Redis network. "
                f"After pytest, run {TEARDOWN_OWNERSHIP['teardown_script']}."
            ),
        },
    }
    # Final guard: refuse writing credential-bearing Redis URLs.
    blob = json.dumps(report)
    if re.search(r"redis://[^/\s]+:[^@/\s]+@", blob):
        raise RuntimeError("REFUSE: report would contain credential-bearing Redis URL.")
    if "PRODUCTION_WORKER_REDIS_URL" in report:
        raise RuntimeError("REFUSE: report must not include PRODUCTION_WORKER_REDIS_URL.")

    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if safe else 1


if __name__ == "__main__":
    raise SystemExit(main())
