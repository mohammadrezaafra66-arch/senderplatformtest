"""Migration preconditions for Rubika L2 canonical session schema.

Used by Alembic upgrade guards and isolated tests. Never auto-canonicalizes.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class LegacyDuplicateInventory:
    account_ids_with_multiple_sessions: list[int]
    total_duplicate_accounts: int


class MigrationPreconditionError(RuntimeError):
    """Raised when L2/L16 preconditions are not met."""


def inventory_legacy_rubika_duplicates(conn: Connection) -> LegacyDuplicateInventory:
    rows = conn.execute(
        text(
            """
            SELECT account_id, COUNT(*) AS n
            FROM channel_sessions
            WHERE session_type::text IN ('rubika_session', 'RUBIKA_SESSION')
            GROUP BY account_id
            HAVING COUNT(*) > 1
            ORDER BY account_id
            """
        )
    ).fetchall()
    ids = [int(r[0]) for r in rows]
    return LegacyDuplicateInventory(
        account_ids_with_multiple_sessions=ids,
        total_duplicate_accounts=len(ids),
    )


def assert_refuses_auto_canonicalize_ambiguous_duplicates(
    conn: Connection,
    *,
    allow_duplicates_as_legacy: bool = True,
) -> LegacyDuplicateInventory:
    """L2 rule: ambiguous legacy duplicates must NOT be auto-resolved.

    When allow_duplicates_as_legacy=True (L2 backfill path), duplicates are
    inventoried but allowed to remain as LEGACY_UNCLASSIFIED.

    When allow_duplicates_as_legacy=False (promotion/canonicalize path), any
    account with >1 LEGACY/candidate row and no explicit operator choice fails.
    """
    inv = inventory_legacy_rubika_duplicates(conn)
    if not allow_duplicates_as_legacy and inv.total_duplicate_accounts > 0:
        raise MigrationPreconditionError(
            "PRECONDITION_FAILED: ambiguous legacy rubika session duplicates exist "
            f"for accounts {inv.account_ids_with_multiple_sessions}. "
            "Operator-approved SET_CANONICAL required (L16). Refusing auto-pick."
        )
    return inv


def assert_no_multiple_active_rubika_sessions(conn: Connection) -> None:
    rows = conn.execute(
        text(
            """
            SELECT account_id, COUNT(*) AS n
            FROM channel_sessions
            WHERE session_status = 'active'
              AND session_type::text IN ('rubika_session', 'RUBIKA_SESSION')
            GROUP BY account_id
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    if rows:
        raise MigrationPreconditionError(
            "PRECONDITION_FAILED: multiple ACTIVE rubika sessions for accounts "
            f"{[int(r[0]) for r in rows]}"
        )
