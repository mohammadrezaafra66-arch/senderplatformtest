"""Fail-fast isolation helpers for hermetic pytest (never production mmp_db)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable
from urllib.parse import urlparse

# Canonical live application database name (Compose POSTGRES_DB).
PRODUCTION_DB_NAME = "mmp_db"

# Allowed ephemeral / hermetic database name prefixes.
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


def assert_test_database_url(url: str | None) -> str:
    """Raise if DATABASE_URL is missing or points at the live application DB."""
    if not url:
        raise RuntimeError("DATABASE_URL is required for DB-backed tests.")
    name = database_name_from_url(url)
    if not name:
        raise RuntimeError("DATABASE_URL has no database name.")
    if name.lower() == PRODUCTION_DB_NAME.lower():
        raise RuntimeError(
            f"REFUSE: DATABASE_URL targets production database '{PRODUCTION_DB_NAME}'. "
            "Use a dedicated ephemeral test database."
        )
    if not any(name.lower().startswith(p.lower()) for p in _TEST_DB_PREFIXES):
        # Still refuse unknown names that look like production aliases.
        if "prod" in name.lower() and "test" not in name.lower() and "isolated" not in name.lower():
            raise RuntimeError(f"REFUSE: suspicious DATABASE_URL database name '{name}'.")
        if name.lower() in {"postgres", "template0", "template1", "evolution_db"}:
            raise RuntimeError(f"REFUSE: DATABASE_URL database '{name}' is not a test DB.")
    return name


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
