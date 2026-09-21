"""Classify ACTIVE Rubika accounts: canonical session vs needs L3 re-login.

Does not mutate ChannelSession, OTP, or promotion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    AccountStatus,
    PlatformType,
    RubikaCanonicalSessionCheck,
)

CANONICAL_OK = "CANONICAL_OK"
NO_ACTIVE_SESSION = "NO_ACTIVE_SESSION"
MULTIPLE_ACTIVE_SESSIONS = "MULTIPLE_ACTIVE_SESSIONS"
NOT_CANONICAL_MANAGED = "NOT_CANONICAL_MANAGED"


@dataclass(frozen=True)
class CanonicalSessionCheckResult:
    account_id: int
    has_canonical_session: bool
    needs_relogin: bool
    reason: str
    session_count: int


def classify_active_rubika_account(db: Session, account: Account) -> CanonicalSessionCheckResult:
    from core_engine.services.rubika_canonical_session import count_active_rubika_sessions
    from core_engine.services.rubika_l17_automation import (
        account_is_canonical_managed,
        count_rubika_sessions,
    )

    total = count_rubika_sessions(db, int(account.id))
    active = count_active_rubika_sessions(db, int(account.id))
    if account_is_canonical_managed(db, int(account.id)):
        return CanonicalSessionCheckResult(
            account_id=int(account.id),
            has_canonical_session=True,
            needs_relogin=False,
            reason=CANONICAL_OK,
            session_count=total,
        )
    if active == 0:
        reason = NO_ACTIVE_SESSION
    elif active > 1:
        reason = MULTIPLE_ACTIVE_SESSIONS
    else:
        reason = NOT_CANONICAL_MANAGED
    return CanonicalSessionCheckResult(
        account_id=int(account.id),
        has_canonical_session=False,
        needs_relogin=True,
        reason=reason,
        session_count=total,
    )


def refresh_canonical_session_checks(db: Session, *, clock: datetime | None = None) -> int:
    """Upsert a snapshot row for every ACTIVE Rubika account. No session writes."""
    now = clock or datetime.now(timezone.utc)
    accounts = (
        db.query(Account)
        .filter(Account.platform == PlatformType.RUBIKA, Account.status == AccountStatus.ACTIVE)
        .all()
    )
    n = 0
    for account in accounts:
        result = classify_active_rubika_account(db, account)
        row = (
            db.query(RubikaCanonicalSessionCheck)
            .filter(RubikaCanonicalSessionCheck.account_id == account.id)
            .first()
        )
        if row is None:
            row = RubikaCanonicalSessionCheck(account_id=int(account.id))
            db.add(row)
        row.has_canonical_session = result.has_canonical_session
        row.needs_relogin = result.needs_relogin
        row.reason = result.reason
        row.session_count = result.session_count
        row.checked_at = now
        n += 1
    db.flush()
    return n
