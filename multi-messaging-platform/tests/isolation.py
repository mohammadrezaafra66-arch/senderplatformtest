"""Fail-fast isolation helpers for hermetic pytest (never production mmp_db)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable
from urllib.parse import urlparse

# Canonical live application database name (Compose POSTGRES_DB).
PRODUCTION_DB_NAME = "mmp_db"

# Names pytest must never use for schema/row writes.
FORBIDDEN_WRITE_DATABASES = frozenset(
    {
        "mmp_isolated_readiness",
        "mmp_db",
        "postgres",
    }
)
PYTEST_DATABASE_PREFIX = "mmp_pytest_"
DEFAULT_PYTEST_DATABASE = "mmp_pytest_campaign_accounts"
REFUSING_NON_TEST_DATABASE = "REFUSING_NON_TEST_DATABASE"
PYTEST_TRUNCATE_LOCK_TIMEOUT = "PYTEST_TRUNCATE_LOCK_TIMEOUT"
_TRUNCATE_LOCK_TIMEOUT = "5s"
_PG_LOCK_NOT_AVAILABLE = "55P03"

# Historical prefixes kept only for fingerprint helpers / legacy docs.
# Postgres-backed pytest writes now require PYTEST_DATABASE_PREFIX.
_TEST_DB_PREFIXES = (
    "mmp_isolated_",
    "mmp_prod_guards_",
    "mmp_phase71_test",
    "mmp_pytest_",
    "mmp_l2_",
    "mmp_l3_",
    "mmp_l4_",
    "mmp_l8_",
)


def database_name_from_url(url: str) -> str:
    path = (urlparse(url).path or "").lstrip("/")
    return path.split("?")[0].strip()


def is_production_database_url(url: str | None) -> bool:
    if not url:
        return False
    name = database_name_from_url(url)
    return name.lower() == PRODUCTION_DB_NAME.lower()


def is_postgres_url(url: str | None) -> bool:
    return bool(url) and url.startswith("postgresql")


def rewrite_database_url(url: str, name: str) -> str:
    from urllib.parse import urlunparse

    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=f"/{name}"))


def assert_pytest_database_name(name: str | None) -> str:
    """Refuse any Postgres database that is not an mmp_pytest_* test database."""
    normalized = str(name or "").strip()
    if (
        not normalized
        or normalized.lower() in {item.lower() for item in FORBIDDEN_WRITE_DATABASES}
        or not normalized.startswith(PYTEST_DATABASE_PREFIX)
    ):
        raise RuntimeError(REFUSING_NON_TEST_DATABASE)
    return normalized


def assert_pytest_database_url(url: str | None) -> str:
    if not url:
        raise RuntimeError(REFUSING_NON_TEST_DATABASE)
    return assert_pytest_database_name(database_name_from_url(url))


def assert_connected_pytest_database(connection) -> str:
    """SELECT current_database() and refuse before any schema/row write."""
    from sqlalchemy import text

    current = connection.execute(text("SELECT current_database()")).scalar()
    return assert_pytest_database_name(str(current or "").strip())


def _is_postgres_lock_timeout(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        pgcode = getattr(current, "pgcode", None) or getattr(current, "sqlstate", None)
        if str(pgcode) == _PG_LOCK_NOT_AVAILABLE:
            return True
        orig = getattr(current, "orig", None)
        if orig is not None and orig is not current:
            current = orig
            continue
        message = str(current).lower()
        if "lock timeout" in message or "lock_not_available" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


def truncate_pytest_database(engine) -> None:
    """Remove leftover pytest rows. Guarded: never truncates forbidden databases."""
    from sqlalchemy import inspect, text

    with engine.connect() as conn:
        current = assert_connected_pytest_database(conn)
        names = inspect(conn).get_table_names()
        if conn.in_transaction():
            conn.commit()
        if not names:
            return
        quoted = ", ".join('"' + name.replace('"', '""') + '"' for name in names)
        try:
            with conn.begin():
                conn.exec_driver_sql(f"SET LOCAL lock_timeout = '{_TRUNCATE_LOCK_TIMEOUT}'")
                conn.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))
        except Exception as exc:
            if _is_postgres_lock_timeout(exc):
                raise RuntimeError(
                    f"{PYTEST_TRUNCATE_LOCK_TIMEOUT}: TRUNCATE on '{current}' waited more than "
                    f"{_TRUNCATE_LOCK_TIMEOUT}; an open session or connection still holds a lock"
                ) from exc
            raise


def ensure_default_pytest_database(source_url: str) -> str:
    """Create mmp_pytest_campaign_accounts if needed. Never create_all here."""
    from sqlalchemy import create_engine, text

    target_url = rewrite_database_url(source_url, DEFAULT_PYTEST_DATABASE)
    last_error: Exception | None = None
    admin_names = ["postgres"]
    source_name = database_name_from_url(source_url)
    if source_name and source_name not in admin_names:
        admin_names.append(source_name)
    for admin_name in admin_names:
        admin = create_engine(
            rewrite_database_url(source_url, admin_name),
            isolation_level="AUTOCOMMIT",
        )
        try:
            with admin.connect() as conn:
                exists = conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": DEFAULT_PYTEST_DATABASE},
                ).scalar()
                if not exists:
                    conn.execute(text(f'CREATE DATABASE "{DEFAULT_PYTEST_DATABASE}"'))
            return target_url
        except Exception as exc:
            last_error = exc
        finally:
            admin.dispose()
    raise RuntimeError(
        f"{REFUSING_NON_TEST_DATABASE}: could not ensure {DEFAULT_PYTEST_DATABASE}"
        + (f" ({type(last_error).__name__})" if last_error else "")
    )


def configure_pytest_database_url() -> str | None:
    """Rewrite process DATABASE_URL to mmp_pytest_* before app/engine import."""
    url = os.environ.get("DATABASE_URL") or ""
    if not is_postgres_url(url):
        return None
    name = database_name_from_url(url)
    if name.startswith(PYTEST_DATABASE_PREFIX):
        assert_pytest_database_name(name)
        return url
    target = ensure_default_pytest_database(url)
    os.environ["DATABASE_URL"] = target
    assert_pytest_database_url(target)
    return target


def rebind_sessionlocal(engine) -> None:
    """Point SessionLocal/engine at the guarded pytest engine."""
    import core_engine.database as database
    from sqlalchemy.orm import sessionmaker

    database.engine = engine
    database.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def assert_test_database_url(url: str | None) -> str:
    """Raise if DATABASE_URL is missing or is not an mmp_pytest_* database."""
    if not url:
        raise RuntimeError("DATABASE_URL is required for DB-backed tests.")
    name = database_name_from_url(url)
    if not name:
        raise RuntimeError("DATABASE_URL has no database name.")
    if name.lower() == PRODUCTION_DB_NAME.lower():
        raise RuntimeError(
            f"REFUSE: DATABASE_URL targets production database '{PRODUCTION_DB_NAME}'. "
            "Use a dedicated ephemeral test database. "
            f"{REFUSING_NON_TEST_DATABASE}"
        )
    try:
        return assert_pytest_database_name(name)
    except RuntimeError as exc:
        if str(exc) == REFUSING_NON_TEST_DATABASE:
            raise RuntimeError(
                f"{REFUSING_NON_TEST_DATABASE}: database '{name}' is not an "
                f"{PYTEST_DATABASE_PREFIX}* pytest database."
            ) from exc
        raise


def redis_host_port_db(url: str | None) -> tuple[str, int, int]:
    if not url:
        raise RuntimeError("REDIS_URL is required for Redis-backed tests.")
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = int(parsed.port or 6379)
    db = 0
    if parsed.path and parsed.path.strip("/"):
        try:
            db = int(parsed.path.strip("/").split("/")[0])
        except ValueError:
            db = 0
    return host, port, db


def assert_test_redis_url(
    url: str | None,
    *,
    production_hosts: frozenset[str] | None = None,
) -> tuple[str, int, int]:
    """Refuse Redis hosts that production workers use (compose service 'redis', DB 0)."""
    host, port, db = redis_host_port_db(url)
    prod_hosts = production_hosts or frozenset(
        {
            "redis",
            "mmp_redis",
        }
    )
    isolated = (
        "test" in host.lower()
        or "iso" in host.lower()
        or host.startswith("mmp_test_redis")
    )
    if host in prod_hosts:
        raise RuntimeError(
            f"REFUSE: REDIS_URL host '{host}' is the production Compose Redis. "
            "Use a dedicated ephemeral test Redis container."
        )
    if not isolated:
        raise RuntimeError(
            f"REFUSE: REDIS_URL host '{host}' is not an approved isolated test Redis "
            "(expected hostname containing 'test'/'iso' or 'mmp_test_redis*')."
        )
    return host, port, db


# --- Production row fingerprints (non-secret metadata only; never phones/tokens) ---
#
# Numeric primary keys alone are NOT canaries: isolated tests may autoincrement
# into the same id space. Fingerprints require compound, production-stable fields.


@dataclass(frozen=True)
class ProductionAccountFingerprint:
    account_id: int
    platform: str  # PlatformType value, e.g. "rubika"
    created_on: date  # UTC calendar date of production created_at


@dataclass(frozen=True)
class ProductionCampaignFingerprint:
    campaign_id: int
    name: str


# From remediation reports (OWNER_CAMPAIGN_DIAGNOSIS / R10 artifacts) — public ops IDs + dates.
PRODUCTION_ACCOUNT_FINGERPRINTS: tuple[ProductionAccountFingerprint, ...] = (
    ProductionAccountFingerprint(12, "rubika", date(2026, 8, 15)),
    ProductionAccountFingerprint(79, "rubika", date(2026, 8, 15)),
)

PRODUCTION_CAMPAIGN_FINGERPRINTS: tuple[ProductionCampaignFingerprint, ...] = (
    ProductionCampaignFingerprint(101, "R10-CONTROLLED-REAL-SEND-2MSG"),
    ProductionCampaignFingerprint(102, "R10-ACCOUNT79-REPLACEMENT-1MSG"),
)


def _normalize_platform(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        return str(value.value).strip().lower()
    text = str(value).strip().lower()
    if text.startswith("platformtype."):
        text = text.split(".", 1)[1]
    return text


def _as_utc_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # Accept "2026-08-15T10:50:44..." or "2026-08-15 10:50:44..."
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def account_matches_production_fingerprint(
    *,
    account_id: int,
    platform: Any,
    created_at: Any,
    fingerprints: Iterable[ProductionAccountFingerprint] = PRODUCTION_ACCOUNT_FINGERPRINTS,
) -> bool:
    """True only when id+platform+created_on all match a known production fingerprint."""
    plat = _normalize_platform(platform)
    created = _as_utc_date(created_at)
    if created is None:
        return False
    for fp in fingerprints:
        if (
            int(account_id) == int(fp.account_id)
            and plat == fp.platform
            and created == fp.created_on
        ):
            return True
    return False


def campaign_matches_production_fingerprint(
    *,
    campaign_id: int,
    name: str | None,
    fingerprints: Iterable[ProductionCampaignFingerprint] = PRODUCTION_CAMPAIGN_FINGERPRINTS,
) -> bool:
    """True only when id+exact campaign name match a known production fingerprint."""
    nm = (name or "").strip()
    for fp in fingerprints:
        if int(campaign_id) == int(fp.campaign_id) and nm == fp.name:
            return True
    return False


def assert_connected_database_is_not_production(connection) -> str:
    """Hard-fail if the live connection's current_database() is mmp_db."""
    from sqlalchemy import text

    current = connection.execute(text("SELECT current_database()")).scalar()
    name = str(current or "").strip()
    if name.lower() == PRODUCTION_DB_NAME.lower():
        raise RuntimeError(
            f"REFUSE: connected database is production '{PRODUCTION_DB_NAME}'."
        )
    return name


def assert_no_production_row_visibility(connection) -> None:
    """Fail if production fingerprints are visible (not mere PK collisions)."""
    from sqlalchemy import text

    assert_connected_database_is_not_production(connection)

    # Accounts: compound fingerprint (id + platform + created_on).
    accounts_exist = connection.execute(
        text("SELECT to_regclass('accounts') IS NOT NULL")
    ).scalar()
    if accounts_exist:
        rows = connection.execute(
            text("SELECT id, platform, created_at FROM accounts")
        ).mappings().all()
        for row in rows:
            if account_matches_production_fingerprint(
                account_id=int(row["id"]),
                platform=row["platform"],
                created_at=row["created_at"],
            ):
                raise RuntimeError(
                    "REFUSE: production account fingerprint visible in connected DB "
                    f"(account_id={row['id']}, platform={row['platform']!s}, "
                    f"created_on={_as_utc_date(row['created_at'])})."
                )

    # Campaigns: compound fingerprint (id + exact non-secret name).
    campaigns_exist = connection.execute(
        text("SELECT to_regclass('campaigns') IS NOT NULL")
    ).scalar()
    if campaigns_exist:
        rows = connection.execute(
            text("SELECT id, name FROM campaigns")
        ).mappings().all()
        for row in rows:
            if campaign_matches_production_fingerprint(
                campaign_id=int(row["id"]),
                name=row["name"],
            ):
                raise RuntimeError(
                    "REFUSE: production campaign fingerprint visible in connected DB "
                    f"(campaign_id={row['id']}, name={row['name']!r})."
                )
