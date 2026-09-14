"""Atomic Rubika session promotion (L1/L2).

Network reconnect/identity prove MUST happen BEFORE calling promote.
This module only performs the short DB transaction:
  old ACTIVE -> SUPERSEDED
  candidate VALIDATING -> ACTIVE
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    ChannelSession,
    RubikaSessionStatus,
    SessionType,
)
from core_engine.services.rubika_identity import (
    IDENTITY_MISMATCH,
    bind_or_verify_rubika_identity,
)

CANDIDATE_NOT_FOUND = "CANDIDATE_NOT_FOUND"
CANDIDATE_WRONG_STATUS = "CANDIDATE_WRONG_STATUS"
CANDIDATE_WRONG_ACCOUNT = "CANDIDATE_WRONG_ACCOUNT"
CANDIDATE_WRONG_TYPE = "CANDIDATE_WRONG_TYPE"
PROMOTION_RACE = "PROMOTION_RACE"
PROMOTION_OK = "PROMOTION_OK"
ACCOUNT_MISSING = "ACCOUNT_MISSING"
IDENTITY_REQUIRED = "IDENTITY_REQUIRED"


@dataclass(frozen=True)
class PromotionResult:
    ok: bool
    code: str
    active_session_id: int | None = None
    superseded_session_id: int | None = None


def promote_validated_session(
    db: Session,
    *,
    account_id: int,
    candidate_session_id: int,
    clock: datetime | None = None,
    bind_identity_if_unbound: bool = True,
) -> PromotionResult:
    """Promote a VALIDATING candidate to ACTIVE in a short DB transaction.

    Preconditions (caller-enforced before network-free promote):
    - candidate decrypt/structure/reconnect already proven
    - candidate.session_status == VALIDATING
    - candidate.identity_guid set when identity checks are required

    Does not perform network I/O.
    """
    now = clock or datetime.now(timezone.utc)
    account = db.query(Account).filter(Account.id == int(account_id)).first()
    if account is None:
        return PromotionResult(ok=False, code=ACCOUNT_MISSING)

    candidate = (
        db.query(ChannelSession)
        .filter(ChannelSession.id == int(candidate_session_id))
        .with_for_update()
        .first()
    )
    if candidate is None:
        return PromotionResult(ok=False, code=CANDIDATE_NOT_FOUND)
    if int(candidate.account_id) != int(account_id):
        return PromotionResult(ok=False, code=CANDIDATE_WRONG_ACCOUNT)
    if candidate.session_type != SessionType.RUBIKA_SESSION:
        return PromotionResult(ok=False, code=CANDIDATE_WRONG_TYPE)
    if candidate.session_status != RubikaSessionStatus.VALIDATING:
        return PromotionResult(ok=False, code=CANDIDATE_WRONG_STATUS)

    guid = str(candidate.identity_guid or "").strip() or None
    if not guid:
        return PromotionResult(ok=False, code=IDENTITY_REQUIRED)

    identity = bind_or_verify_rubika_identity(
        account,
        guid,
        allow_bind_if_unbound=bind_identity_if_unbound,
        clock=now,
    )
    if not identity.ok:
        # Do not mutate session statuses on identity failure.
        db.flush()
        return PromotionResult(ok=False, code=identity.code)

    old_active = (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == int(account_id),
            ChannelSession.session_type == SessionType.RUBIKA_SESSION,
            ChannelSession.session_status == RubikaSessionStatus.ACTIVE,
        )
        .with_for_update()
        .all()
    )
    superseded_id = None
    if len(old_active) > 1:
        # Invariant already broken — refuse to make it worse.
        return PromotionResult(ok=False, code="MULTIPLE_ACTIVE_SESSIONS")
    if len(old_active) == 1:
        old = old_active[0]
        if int(old.id) == int(candidate.id):
            return PromotionResult(
                ok=True,
                code=PROMOTION_OK,
                active_session_id=int(candidate.id),
            )
        old.session_status = RubikaSessionStatus.SUPERSEDED
        old.superseded_at = now
        superseded_id = int(old.id)

    candidate.session_status = RubikaSessionStatus.ACTIVE
    candidate.validated_at = now
    candidate.validation_error_code = None
    candidate.invalidated_at = None

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return PromotionResult(ok=False, code=PROMOTION_RACE)

    from core_engine.services.rubika_account_lifecycle import (
        activate_rubika_account_after_validated_session,
    )

    activate_rubika_account_after_validated_session(account)
    db.flush()

    return PromotionResult(
        ok=True,
        code=PROMOTION_OK,
        active_session_id=int(candidate.id),
        superseded_session_id=superseded_id,
    )


# Re-export mismatch code for callers/tests
__all__ = [
    "PromotionResult",
    "promote_validated_session",
    "PROMOTION_OK",
    "CANDIDATE_NOT_FOUND",
    "CANDIDATE_WRONG_STATUS",
    "IDENTITY_MISMATCH",
    "IDENTITY_REQUIRED",
    "PROMOTION_RACE",
]
