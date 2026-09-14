"""Exact-name teardown for fully isolated pytest topology.

Removes ONLY (exact names):
  - Docker container mmp_test_postgres_iso
  - Docker container mmp_test_redis_iso
  - Docker network mmp_test_isolation

Optional legacy cleanup (explicit flag only):
  - DROP DATABASE mmp_isolated_pytest on host mmp_postgres
    (from earlier isolation-proof; never drops mmp_db)

Hard-refuses any other name. Never removes mmp_postgres, mmp_redis,
or multi-messaging-platform_default.

Does NOT run pytest, start workers, enqueue, or send.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

PRODUCTION_DB_NAME = "mmp_db"
TEST_DB_NAME = "mmp_isolated_pytest"
TEST_REDIS_NAME = "mmp_test_redis_iso"
TEST_POSTGRES_NAME = "mmp_test_postgres_iso"
TEST_NETWORK = "mmp_test_isolation"
PROD_NETWORK = "multi-messaging-platform_default"
PROD_REDIS_CONTAINER = "mmp_redis"
PROD_POSTGRES_CONTAINER = "mmp_postgres"


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _refuse_if_not_exact(actual: str, expected: str, *, kind: str) -> None:
    if actual != expected:
        raise SystemExit(
            f"REFUSE: {kind} '{actual}' != exact allowed '{expected}'."
        )


def teardown_container(*, name: str, expected: str, kind: str) -> None:
    _refuse_if_not_exact(name, expected, kind=kind)
    forbidden = {
        PROD_REDIS_CONTAINER,
        PROD_POSTGRES_CONTAINER,
        "redis",
        "postgres",
        "mmp_core_api",
        "mmp_rubika_worker",
    }
    if name in forbidden:
        raise SystemExit(f"REFUSE: will not remove production/service container '{name}'.")
    exists = _run(["docker", "inspect", name], check=False)
    if exists.returncode != 0:
        print(f"{kind}_absent={name}")
        return
    _run(["docker", "rm", "-f", name])
    print(f"{kind}_removed={name}")


def teardown_network(*, name: str = TEST_NETWORK) -> None:
    _refuse_if_not_exact(name, TEST_NETWORK, kind="docker_network")
    if name == PROD_NETWORK:
        raise SystemExit(f"REFUSE: will not remove production network '{name}'.")
    exists = _run(["docker", "network", "inspect", name], check=False)
    if exists.returncode != 0:
        print(f"network_absent={name}")
        return
    _run(["docker", "network", "rm", name])
    print(f"network_removed={name}")


def teardown_legacy_db_on_mmp_postgres(*, name: str = TEST_DB_NAME) -> None:
    """Optional: drop leftover test DB created on production Postgres server."""
    _refuse_if_not_exact(name, TEST_DB_NAME, kind="database")
    if name == PRODUCTION_DB_NAME:
        raise SystemExit(f"REFUSE: will not drop production database '{name}'.")
    drop_sql = f"DROP DATABASE IF EXISTS {TEST_DB_NAME};"
    if drop_sql != "DROP DATABASE IF EXISTS mmp_isolated_pytest;":
        raise SystemExit("REFUSE: DROP SQL does not match exact test DB literal.")
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
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            (
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{TEST_DB_NAME}' AND pid <> pg_backend_pid();"
            ),
        ],
        check=False,
    )
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
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            drop_sql,
        ]
    )
    print(f"legacy_database_dropped_on_mmp_postgres={TEST_DB_NAME}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exact-name isolation teardown")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required. Confirms teardown of exact test-only resources.",
    )
    parser.add_argument("--skip-postgres", action="store_true")
    parser.add_argument("--skip-redis", action="store_true")
    parser.add_argument("--skip-network", action="store_true")
    parser.add_argument(
        "--legacy-mmp-postgres-db",
        action="store_true",
        help="Also DROP mmp_isolated_pytest on mmp_postgres (legacy proof DB only).",
    )
    args = parser.parse_args(argv)
    if not args.confirm:
        print(
            "REFUSE: pass --confirm to tear down exact test resources "
            f"({TEST_POSTGRES_NAME}, {TEST_REDIS_NAME}, {TEST_NETWORK}).",
            file=sys.stderr,
        )
        return 2

    # Containers first (release network), then network.
    if not args.skip_postgres:
        teardown_container(
            name=TEST_POSTGRES_NAME,
            expected=TEST_POSTGRES_NAME,
            kind="postgres_container",
        )
    if not args.skip_redis:
        teardown_container(
            name=TEST_REDIS_NAME,
            expected=TEST_REDIS_NAME,
            kind="redis_container",
        )
    if not args.skip_network:
        teardown_network(name=TEST_NETWORK)
    if args.legacy_mmp_postgres_db:
        teardown_legacy_db_on_mmp_postgres(name=TEST_DB_NAME)
    print("TEARDOWN_COMPLETE=True")
    print("TEARDOWN_EXACT_NAMES_ONLY=True")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
