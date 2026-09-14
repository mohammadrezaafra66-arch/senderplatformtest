#!/usr/bin/env python3
"""L17 — Permanent new-account automation decision helpers.

Centralized routing / enrollment / scope decisions. Call sites must not
duplicate evidence rules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountStatus,
    ChannelSession,
    PlatformType,
    RubikaAccountPool,
    RubikaIdentityStatus,
    RubikaSessionStatus,
    SessionType,
)

logger = logging.getLogger("core_engine.rubika_l17_automation")

# Login routing modes (core_engine.config.RUBIKA_L3_LOGIN_ROUTING)
L3_ROUTING_PILOT = "pilot"
L3_ROUTING_AUTO_EVIDENCE = "auto_evidence"
VALID_L3_ROUTING = frozenset({L3_ROUTING_PILOT, L3_ROUTING_AUTO_EVIDENCE})

# Discovery scope (workers + mirrored on core for simulation)
DISCOVERY_SCOPE_COHORT = "cohort"
DISCOVERY_SCOPE_ALL_ELIGIBLE = "all_eligible"
VALID_DISCOVERY_SCOPE = frozenset({DISCOVERY_SCOPE_COHORT, DISCOVERY_SCOPE_ALL_ELIGIBLE})

# Canonical enforce scope
ENFORCE_SCOPE_ALLOWLIST = "allowlist"
ENFORCE_SCOPE_CANONICAL_ACTIVE = "canonical_active"
VALID_ENFORCE_SCOPE = frozenset({ENFORCE_SCOPE_ALLOWLIST, ENFORCE_SCOPE_CANONICAL_ACTIVE})

POOL_ENROLLMENT_FAILED = "POOL_ENROLLMENT_FAILED"
POOL_ENROLLMENT_OK = "POOL_ENROLLMENT_OK"
POOL_ENROLLMENT_SKIPPED = "POOL_ENROLLMENT_SKIPPED"


def normalize_l3_routing(raw: str | None) -> str:
    mode = str(raw or L3_ROUTING_PILOT).strip().lower() or L3_ROUTING_PILOT
    return mode if mode in VALID_L3_ROUTING else L3_ROUTING_PILOT


def normalize_discovery_scope(raw: str | None) -> str:
    scope = str(raw or DISCOVERY_SCOPE_COHORT).strip().lower() or DISCOVERY_SCOPE_COHORT
    return scope if scope in VALID_DISCOVERY_SCOPE else DISCOVERY_SCOPE_COHORT


def normalize_enforce_scope(raw: str | None) -> str:
    scope = str(raw or ENFORCE_SCOPE_ALLOWLIST).strip().lower() or ENFORCE_SCOPE_ALLOWLIST
    return scope if scope in VALID_ENFORCE_SCOPE else ENFORCE_SCOPE_ALLOWLIST


def count_rubika_sessions(db: Session, account_id: int) -> int:
    return (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == int(account_id),
            ChannelSession.session_type == SessionType.RUBIKA_SESSION,
        )
        .count()
    )


def list_active_rubika_sessions(db: Session, account_id: int) -> list[ChannelSession]:
    return (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == int(account_id),
            ChannelSession.session_type == SessionType.RUBIKA_SESSION,
            ChannelSession.session_status == RubikaSessionStatus.ACTIVE,
        )
        .order_by(ChannelSession.id.asc())
        .all()
    )


def account_is_canonical_managed(db: Session, account_id: int) -> bool:
    """Exactly one ACTIVE Rubika session with usable identity binding evidence."""
    account = db.query(Account).filter(Account.id == int(account_id)).first()
    if account is None or account.platform != PlatformType.RUBIKA:
        return False
    actives = list_active_rubika_sessions(db, account_id)
    if len(actives) != 1:
        return False
    row = actives[0]
    guid = str(row.identity_guid or "").strip()
    bound = str(account.rubika_guid or "").strip()
    status = account.rubika_identity_status
    if not guid or not bound:
        return False
    if status in {None, RubikaIdentityStatus.UNBOUND, RubikaIdentityStatus.MISMATCH_LOCKED}:
        return False
    if guid != bound:
        return False
    return True


def account_has_zero_sessions(db: Session, account_id: int) -> bool:
    return count_rubika_sessions(db, account_id) == 0


def account_is_legacy_protected(db: Session, account_id: int) -> bool:
    """True when account has legacy inventory that must not auto-route to L3.

    LEGACY_UNCLASSIFIED (or any mix that is not pure L3 debris / canonical ACTIVE)
    stays on legacy login until separately canonicalized.
    """
    if account_has_zero_sessions(db, account_id):
        return False
    if account_is_canonical_managed(db, account_id):
        return False
    rows = (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == int(account_id),
            ChannelSession.session_type == SessionType.RUBIKA_SESSION,
        )
        .all()
    )
    # Any legacy-unclassified inventory → protect
    if any(r.session_status == RubikaSessionStatus.LEGACY_UNCLASSIFIED for r in rows):
        return True
    # Ambiguous multiple ACTIVE should never happen; protect if present
    if len(list_active_rubika_sessions(db, account_id)) > 1:
        return True
    # Pure L3 lifecycle debris (VALIDATING/INVALID/…) without ACTIVE → not legacy-protected
    return False


def account_allows_l3_retry_debris(db: Session, account_id: int) -> bool:
    """Failed/in-progress L3 attempts with no LEGACY_UNCLASSIFIED rows may retry L3."""
    if account_has_zero_sessions(db, account_id) or account_is_canonical_managed(db, account_id):
        return False
    return not account_is_legacy_protected(db, account_id)


@dataclass(frozen=True)
class L3RoutingDecision:
    use_l3: bool
    reason: str


def decide_l3_login_routing(db: Session, account_id: int) -> L3RoutingDecision:
    """Single authoritative L3 vs legacy login routing decision."""
    from core_engine.services.rubika_login_state_machine import parse_login_pilot_account_ids

    settings = get_settings()
    if bool(settings.RUBIKA_CANONICAL_SESSION_V1):
        return L3RoutingDecision(True, "global_v1")

    pilot = parse_login_pilot_account_ids(
        getattr(settings, "RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS", "")
    )
    if int(account_id) in pilot:
        return L3RoutingDecision(True, "pilot_override")

    routing = normalize_l3_routing(getattr(settings, "RUBIKA_L3_LOGIN_ROUTING", None))
    if routing != L3_ROUTING_AUTO_EVIDENCE:
        return L3RoutingDecision(False, "pilot_mode_not_listed")

    if account_has_zero_sessions(db, account_id):
        return L3RoutingDecision(True, "zero_sessions_new_account")
    if account_is_canonical_managed(db, account_id):
        return L3RoutingDecision(True, "canonical_managed")
    if account_allows_l3_retry_debris(db, account_id):
        return L3RoutingDecision(True, "l3_lifecycle_retry")
    return L3RoutingDecision(False, "legacy_protected")


def account_uses_l3_login(account_id: int, db: Session | None = None) -> bool:
    """Backward-compatible wrapper; prefers evidence routing when db provided."""
    settings = get_settings()
    if bool(settings.RUBIKA_CANONICAL_SESSION_V1):
        return True
    from core_engine.services.rubika_login_state_machine import parse_login_pilot_account_ids

    if int(account_id) in parse_login_pilot_account_ids(
        getattr(settings, "RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS", "")
    ):
        return True
    routing = normalize_l3_routing(getattr(settings, "RUBIKA_L3_LOGIN_ROUTING", None))
    if routing != L3_ROUTING_AUTO_EVIDENCE:
        return False
    if db is None:
        # Without DB evidence, refuse to guess — fail closed to legacy unless pilot/V1.
        return False
    return decide_l3_login_routing(db, account_id).use_l3


@dataclass(frozen=True)
class PoolEnrollResult:
    ok: bool
    code: str
    phase: str | None = None
    created: bool = False
    message: str = ""


def ensure_rubika_pool_membership(
    db: Session,
    account_id: int,
    *,
    phase: str | None = None,
) -> PoolEnrollResult:
    """Idempotent pool enrollment after successful L3 canonical promotion.

    Never mutates ChannelSession rows. Safe to call repeatedly.
    """
    settings = get_settings()
    if not bool(getattr(settings, "AUTO_ENROLL_RUBIKA_POOL", False)):
        return PoolEnrollResult(ok=True, code=POOL_ENROLLMENT_SKIPPED, message="flag_off")

    account = db.query(Account).filter(Account.id == int(account_id)).first()
    if account is None:
        return PoolEnrollResult(ok=False, code=POOL_ENROLLMENT_FAILED, message="account_missing")
    if account.platform != PlatformType.RUBIKA:
        return PoolEnrollResult(ok=False, code=POOL_ENROLLMENT_FAILED, message="not_rubika")
    if account.status != AccountStatus.ACTIVE:
        return PoolEnrollResult(ok=False, code=POOL_ENROLLMENT_FAILED, message="account_not_active")
    if account.rubika_identity_status == RubikaIdentityStatus.MISMATCH_LOCKED:
        return PoolEnrollResult(ok=False, code=POOL_ENROLLMENT_FAILED, message="identity_locked")
    if not account_is_canonical_managed(db, account_id):
        return PoolEnrollResult(ok=False, code=POOL_ENROLLMENT_FAILED, message="not_canonical_managed")

    target_phase = (phase or "").strip() or None
    if target_phase is None:
        from workers.rubika_account_pool import resolve_current_phase

        target_phase = resolve_current_phase(db) or str(
            getattr(settings, "DEFAULT_RUBIKA_POOL", "day") or "day"
        )

    existing = (
        db.query(RubikaAccountPool)
        .filter(
            RubikaAccountPool.account_id == int(account_id),
            RubikaAccountPool.phase == str(target_phase),
        )
        .first()
    )
    if existing is not None:
        return PoolEnrollResult(
            ok=True,
            code=POOL_ENROLLMENT_OK,
            phase=str(target_phase),
            created=False,
            message="already_member",
        )

    try:
        # Savepoint so enrollment failure cannot undo outer ACTIVE promotion.
        with db.begin_nested():
            row = RubikaAccountPool(
                account_id=int(account_id),
                phase=str(target_phase),
                priority=100,
            )
            db.add(row)
            db.flush()
    except Exception as exc:  # noqa: BLE001
        # Unique race → treat as already enrolled if row now exists.
        raced = (
            db.query(RubikaAccountPool)
            .filter(
                RubikaAccountPool.account_id == int(account_id),
                RubikaAccountPool.phase == str(target_phase),
            )
            .first()
        )
        if raced is not None:
            return PoolEnrollResult(
                ok=True,
                code=POOL_ENROLLMENT_OK,
                phase=str(target_phase),
                created=False,
                message="already_member_race",
            )
        logger.exception(
            "event=pool_enrollment_failed account_id=%s phase=%s err=%s",
            account_id,
            target_phase,
            type(exc).__name__,
        )
        return PoolEnrollResult(
            ok=False,
            code=POOL_ENROLLMENT_FAILED,
            phase=str(target_phase),
            message=type(exc).__name__,
        )

    return PoolEnrollResult(
        ok=True,
        code=POOL_ENROLLMENT_OK,
        phase=str(target_phase),
        created=True,
        message="enrolled",
    )


def account_should_auto_enforce(db: Session, account_id: int) -> bool:
    """Evidence rule for RUBIKA_CANONICAL_SESSION_SCOPE=canonical_active."""
    return account_is_canonical_managed(db, account_id)


def as_safe_routing_dict(decision: L3RoutingDecision, account_id: int) -> dict[str, Any]:
    return {
        "account_id": int(account_id),
        "use_l3": decision.use_l3,
        "reason": decision.reason,
    }
