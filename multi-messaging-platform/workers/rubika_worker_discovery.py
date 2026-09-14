"""L11 — Controlled Rubika worker discovery (pinned | shadow | dynamic).

Discovery decides which accounts get worker coverage (queue poll + heartbeat).
Session selection remains in ``session_access`` / canonical runtime — never here.

Eligibility is stricter than historical pool-only discovery: pool membership alone
must not enroll every Rubika account row.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Iterable

from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    AccountStatus,
    ChannelSession,
    PlatformType,
    RubikaAccountPool,
    RubikaSessionStatus,
)
from workers.account_pool import parse_account_id_list

logger = logging.getLogger("workers.rubika_worker_discovery")

MODE_PINNED = "pinned"
MODE_SHADOW = "shadow"
MODE_DYNAMIC = "dynamic"
VALID_MODES = frozenset({MODE_PINNED, MODE_SHADOW, MODE_DYNAMIC})


class ExclusionReason(str, Enum):
    NOT_RUBIKA = "NOT_RUBIKA"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    ACCOUNT_BANNED = "ACCOUNT_BANNED"
    ACCOUNT_REQUIRES_LOGIN = "ACCOUNT_REQUIRES_LOGIN"
    ACCOUNT_RESTING = "ACCOUNT_RESTING"
    ACCOUNT_ARCHIVED = "ACCOUNT_ARCHIVED"
    NO_SCHEDULE = "NO_SCHEDULE"
    NOT_IN_POOL = "NOT_IN_POOL"
    SESSION_MISSING = "SESSION_MISSING"
    SESSION_DECRYPT_FAILED = "SESSION_DECRYPT_FAILED"
    SESSION_INVALID = "SESSION_INVALID"
    DUPLICATE_ACTIVE_SESSIONS = "DUPLICATE_ACTIVE_SESSIONS"
    AMBIGUOUS_LEGACY_SESSIONS = "AMBIGUOUS_LEGACY_SESSIONS"
    ACTIVE_SESSION_EMPTY = "ACTIVE_SESSION_EMPTY"
    UNRESOLVED_IDENTITY = "UNRESOLVED_IDENTITY"
    COHORT_EXCLUDED = "COHORT_EXCLUDED"
    NONE = ""


@dataclass(frozen=True)
class AccountDiscoveryClass:
    account_id: int
    account_status: str
    pool_membership: str
    schedule_eligibility: str
    session_availability: str
    session_readiness: str
    canonical_status: str
    legacy_runtime_status: str
    worker_coverage_status: str
    dispatch_readiness: bool
    exclusion_reason: str
    eligible: bool

    def as_safe_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DiscoverySnapshot:
    mode: str
    pinned_ids: list[int]
    dynamic_eligible_ids: list[int]
    actual_worker_ids: list[int]
    would_add: list[int]
    would_remove: list[int]
    cohort_ids: list[int]
    classifications: list[AccountDiscoveryClass] = field(default_factory=list)
    mutated_db: bool = False
    mutated_redis: bool = False

    def as_safe_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "pinned_ids": list(self.pinned_ids),
            "dynamic_eligible_ids": list(self.dynamic_eligible_ids),
            "actual_worker_ids": list(self.actual_worker_ids),
            "would_add": list(self.would_add),
            "would_remove": list(self.would_remove),
            "cohort_ids": list(self.cohort_ids),
            "classifications": [c.as_safe_dict() for c in self.classifications],
            "mutated_db": self.mutated_db,
            "mutated_redis": self.mutated_redis,
        }


def normalize_discovery_mode(raw: str | None) -> str:
    mode = str(raw or MODE_PINNED).strip().lower() or MODE_PINNED
    if mode not in VALID_MODES:
        raise ValueError(
            "RUBIKA_WORKER_DISCOVERY_MODE must be 'pinned', 'shadow', or 'dynamic'."
        )
    return mode


def parse_cohort_ids(raw: str | None) -> list[int]:
    """Empty cohort means no temporary filter (all eligible when mode=dynamic)."""
    return parse_account_id_list(raw or "")


def _session_rows(db: Session, account_id: int) -> list[ChannelSession]:
    return (
        db.query(ChannelSession)
        .filter(ChannelSession.account_id == int(account_id))
        .order_by(ChannelSession.id.asc())
        .all()
    )


def _active_sessions(rows: list[ChannelSession]) -> list[ChannelSession]:
    out: list[ChannelSession] = []
    for row in rows:
        status = row.session_status
        value = status.value if isinstance(status, Enum) else str(status or "")
        if value.lower() == RubikaSessionStatus.ACTIVE.value:
            out.append(row)
    return out


def _has_material(row: ChannelSession) -> bool:
    ct = (row.ciphertext or "").strip()
    fp = (row.file_path or "").strip()
    return bool(ct or fp)


def evaluate_session_availability_for_discovery(
    db: Session,
    account_id: int,
    *,
    account: Account | None = None,
) -> tuple[bool, str, str]:
    """Session availability for worker coverage — does NOT select a send session.

    Never chooses among multiple rows by max(id) for dispatch. Ambiguous
    multi-legacy or duplicate ACTIVE accounts are excluded.
    """
    rows = _session_rows(db, account_id)
    active = _active_sessions(rows)
    if len(active) > 1:
        return False, "duplicate_active", ExclusionReason.DUPLICATE_ACTIVE_SESSIONS.value
    if len(active) == 1:
        row = active[0]
        if not _has_material(row):
            return False, "active_empty", ExclusionReason.ACTIVE_SESSION_EMPTY.value
        guid = (getattr(account, "rubika_guid", None) or "").strip() if account else ""
        identity = (row.identity_guid or "").strip()
        if not guid and not identity:
            return False, "unresolved_identity", ExclusionReason.UNRESOLVED_IDENTITY.value
        return True, "canonical_active", ExclusionReason.NONE.value

    if not rows:
        return False, "missing", ExclusionReason.SESSION_MISSING.value
    if len(rows) > 1:
        # Multiple non-ACTIVE sessions → manual review / ambiguous (e.g. Account12).
        return False, "ambiguous_legacy", ExclusionReason.AMBIGUOUS_LEGACY_SESSIONS.value

    # Exactly one non-ACTIVE session: decryptability check only (that sole row).
    row = rows[0]
    if not _has_material(row):
        return False, "legacy_empty", ExclusionReason.SESSION_MISSING.value
    try:
        from core_engine.services.session_storage import load_channel_session_plaintext

        plaintext = load_channel_session_plaintext(row)
        if not plaintext:
            return False, "decrypt_empty", ExclusionReason.SESSION_DECRYPT_FAILED.value
    except Exception:  # noqa: BLE001
        return False, "decrypt_failed", ExclusionReason.SESSION_DECRYPT_FAILED.value
    return True, "single_legacy", ExclusionReason.NONE.value


def classify_rubika_account_for_discovery(
    db: Session,
    account: Account,
    *,
    current_phase: str | None,
    pool_phases: set[str],
) -> AccountDiscoveryClass:
    aid = int(account.id)
    status = (
        account.status.value
        if isinstance(account.status, Enum)
        else str(account.status or "")
    )
    pool_membership = ",".join(sorted(pool_phases)) if pool_phases else "NONE"

    if account.platform != PlatformType.RUBIKA:
        return AccountDiscoveryClass(
            account_id=aid,
            account_status=status,
            pool_membership=pool_membership,
            schedule_eligibility="n/a",
            session_availability="n/a",
            session_readiness="n/a",
            canonical_status="n/a",
            legacy_runtime_status="n/a",
            worker_coverage_status="excluded",
            dispatch_readiness=False,
            exclusion_reason=ExclusionReason.NOT_RUBIKA.value,
            eligible=False,
        )

    if account.status == AccountStatus.BANNED:
        excl = ExclusionReason.ACCOUNT_BANNED.value
    elif getattr(account, "archived_at", None) is not None:
        excl = ExclusionReason.ACCOUNT_ARCHIVED.value
    elif account.status == AccountStatus.REQUIRES_LOGIN:
        excl = ExclusionReason.ACCOUNT_REQUIRES_LOGIN.value
    elif account.status == AccountStatus.RESTING:
        excl = ExclusionReason.ACCOUNT_RESTING.value
    elif account.status != AccountStatus.ACTIVE:
        excl = ExclusionReason.ACCOUNT_DISABLED.value
    else:
        excl = ""

    if excl:
        return AccountDiscoveryClass(
            account_id=aid,
            account_status=status,
            pool_membership=pool_membership,
            schedule_eligibility="blocked",
            session_availability="unchecked",
            session_readiness="unchecked",
            canonical_status="unchecked",
            legacy_runtime_status="unchecked",
            worker_coverage_status="excluded",
            dispatch_readiness=False,
            exclusion_reason=excl,
            eligible=False,
        )

    if current_phase is None:
        return AccountDiscoveryClass(
            account_id=aid,
            account_status=status,
            pool_membership=pool_membership,
            schedule_eligibility="NO_SCHEDULE",
            session_availability="unchecked",
            session_readiness="unchecked",
            canonical_status="unchecked",
            legacy_runtime_status="unchecked",
            worker_coverage_status="excluded",
            dispatch_readiness=False,
            exclusion_reason=ExclusionReason.NO_SCHEDULE.value,
            eligible=False,
        )

    in_pool = current_phase in pool_phases
    schedule_eligibility = "in_phase_pool" if in_pool else "NOT_IN_POOL"
    if not in_pool:
        return AccountDiscoveryClass(
            account_id=aid,
            account_status=status,
            pool_membership=pool_membership,
            schedule_eligibility=schedule_eligibility,
            session_availability="unchecked",
            session_readiness="unchecked",
            canonical_status="unchecked",
            legacy_runtime_status="unchecked",
            worker_coverage_status="excluded",
            dispatch_readiness=False,
            exclusion_reason=ExclusionReason.NOT_IN_POOL.value,
            eligible=False,
        )

    ok, session_availability, session_excl = evaluate_session_availability_for_discovery(
        db, aid, account=account
    )
    rows = _session_rows(db, aid)
    active = _active_sessions(rows)
    if len(active) == 1:
        canonical_status = f"ACTIVE:{active[0].id}"
        legacy_runtime_status = "canonical_active_present"
    elif not rows:
        canonical_status = "NO_SESSION"
        legacy_runtime_status = "none"
    elif len(rows) == 1:
        canonical_status = "NO_ACTIVE"
        legacy_runtime_status = f"single_legacy:{rows[0].id}"
    else:
        canonical_status = "NO_ACTIVE"
        legacy_runtime_status = f"multi_legacy:{len(rows)}"

    if not ok:
        return AccountDiscoveryClass(
            account_id=aid,
            account_status=status,
            pool_membership=pool_membership,
            schedule_eligibility=schedule_eligibility,
            session_availability=session_availability,
            session_readiness="not_ready",
            canonical_status=canonical_status,
            legacy_runtime_status=legacy_runtime_status,
            worker_coverage_status="excluded",
            dispatch_readiness=False,
            exclusion_reason=session_excl,
            eligible=False,
        )

    return AccountDiscoveryClass(
        account_id=aid,
        account_status=status,
        pool_membership=pool_membership,
        schedule_eligibility=schedule_eligibility,
        session_availability=session_availability,
        session_readiness="ready",
        canonical_status=canonical_status,
        legacy_runtime_status=legacy_runtime_status,
        worker_coverage_status="eligible",
        dispatch_readiness=True,
        exclusion_reason="",
        eligible=True,
    )


def get_dispatch_eligible_rubika_account_ids(
    db: Session,
    *,
    clock: datetime | None = None,
    cohort_ids: Iterable[int] | None = None,
) -> list[int]:
    """Authoritative eligibility set for Rubika worker coverage.

    Predicates (all required):
    1. platform == RUBIKA
    2. account.status == ACTIVE
    3. current send phase resolvable
    4. membership in rubika_account_pool for current phase
    5. session availability (single ACTIVE with material+identity, OR single
       decryptable legacy session — never multi-ambiguous / duplicate ACTIVE)
    6. optional temporary cohort filter when cohort_ids is a non-empty iterable
    """
    from workers.rubika_account_pool import resolve_current_phase

    phase = resolve_current_phase(db, clock=clock)
    if phase is None:
        return []

    accounts = (
        db.query(Account)
        .filter(
            Account.platform == PlatformType.RUBIKA,
            Account.archived_at.is_(None),
        )
        .order_by(Account.id.asc())
        .all()
    )
    pool_rows = db.query(RubikaAccountPool).all()
    pool_by_account: dict[int, set[str]] = {}
    for row in pool_rows:
        pool_by_account.setdefault(int(row.account_id), set()).add(str(row.phase))

    cohort: set[int] | None = None
    if cohort_ids is not None:
        cohort_list = [int(x) for x in cohort_ids]
        if cohort_list:
            cohort = set(cohort_list)

    eligible: list[int] = []
    for account in accounts:
        classification = classify_rubika_account_for_discovery(
            db,
            account,
            current_phase=phase,
            pool_phases=pool_by_account.get(int(account.id), set()),
        )
        if not classification.eligible:
            continue
        if cohort is not None and int(account.id) not in cohort:
            continue
        eligible.append(int(account.id))
    return eligible


def inventory_rubika_discovery(
    db: Session,
    *,
    clock: datetime | None = None,
) -> list[AccountDiscoveryClass]:
    from workers.rubika_account_pool import resolve_current_phase

    phase = resolve_current_phase(db, clock=clock)
    accounts = (
        db.query(Account)
        .filter(Account.platform == PlatformType.RUBIKA)
        .order_by(Account.id.asc())
        .all()
    )
    pool_rows = db.query(RubikaAccountPool).all()
    pool_by_account: dict[int, set[str]] = {}
    for row in pool_rows:
        pool_by_account.setdefault(int(row.account_id), set()).add(str(row.phase))

    return [
        classify_rubika_account_for_discovery(
            db,
            account,
            current_phase=phase,
            pool_phases=pool_by_account.get(int(account.id), set()),
        )
        for account in accounts
    ]


def resolve_actual_worker_account_ids(
    *,
    mode: str,
    pinned_ids: list[int],
    dynamic_eligible_ids: list[int],
    cohort_ids: list[int] | None = None,
    discovery_scope: str | None = None,
) -> list[int]:
    """Resolve the account IDs that must actually receive worker coverage.

    pinned / shadow: pin list only (shadow never expands coverage).

    dynamic + scope=cohort (default, backward-compatible):
      - non-empty cohort: pin ∪ (dynamic ∩ cohort)
      - empty cohort: full dynamic only (historical empty-cohort behavior)

    dynamic + scope=all_eligible (L17 permanent):
      pin ∪ dynamic eligible (cohort ignored for inclusion; pin preserved)
    """
    from core_engine.services.rubika_l17_automation import (
        DISCOVERY_SCOPE_ALL_ELIGIBLE,
        normalize_discovery_scope,
    )

    mode_n = normalize_discovery_mode(mode)
    pinned = sorted({int(x) for x in pinned_ids})
    dynamic = sorted({int(x) for x in dynamic_eligible_ids})
    cohort = [int(x) for x in (cohort_ids or [])]
    scope = normalize_discovery_scope(discovery_scope)

    if mode_n in {MODE_PINNED, MODE_SHADOW}:
        return pinned

    if scope == DISCOVERY_SCOPE_ALL_ELIGIBLE:
        return sorted(set(pinned) | set(dynamic))

    if cohort:
        newly = [a for a in dynamic if a in set(cohort)]
        return sorted(set(pinned) | set(newly))
    return dynamic


def compare_discovery_sets(
    *,
    mode: str,
    pinned_ids: list[int],
    dynamic_eligible_ids: list[int],
    cohort_ids: list[int] | None = None,
    classifications: list[AccountDiscoveryClass] | None = None,
) -> DiscoverySnapshot:
    pinned = sorted({int(x) for x in pinned_ids})
    dynamic = sorted({int(x) for x in dynamic_eligible_ids})
    actual = resolve_actual_worker_account_ids(
        mode=mode,
        pinned_ids=pinned,
        dynamic_eligible_ids=dynamic,
        cohort_ids=cohort_ids,
    )
    pinned_set = set(pinned)
    dynamic_set = set(dynamic)
    return DiscoverySnapshot(
        mode=normalize_discovery_mode(mode),
        pinned_ids=pinned,
        dynamic_eligible_ids=dynamic,
        actual_worker_ids=actual,
        would_add=sorted(dynamic_set - pinned_set),
        would_remove=sorted(pinned_set - dynamic_set),
        cohort_ids=sorted({int(x) for x in (cohort_ids or [])}),
        classifications=list(classifications or []),
        mutated_db=False,
        mutated_redis=False,
    )


def reconcile_worker_account_ids(
    current_ids: list[int],
    desired_ids: list[int],
) -> tuple[list[int], list[int], list[int]]:
    """Idempotent reconcile: returns (next_ids, added, removed)."""
    current = sorted({int(x) for x in current_ids})
    desired = sorted({int(x) for x in desired_ids})
    cur_s, des_s = set(current), set(desired)
    added = sorted(des_s - cur_s)
    removed = sorted(cur_s - des_s)
    return desired, added, removed


def build_discovery_snapshot_from_settings(
    db: Session,
    *,
    mode: str,
    pinned_raw: str,
    cohort_raw: str = "",
    clock: datetime | None = None,
) -> DiscoverySnapshot:
    """Read-only discovery snapshot (no Redis/DB writes)."""
    pinned = parse_account_id_list(pinned_raw or "")
    cohort = parse_cohort_ids(cohort_raw)
    classifications = inventory_rubika_discovery(db, clock=clock)
    # Full eligible set ignores cohort; cohort applies only to actual dynamic coverage.
    dynamic = [
        c.account_id for c in classifications if c.eligible
    ]
    if cohort and normalize_discovery_mode(mode) == MODE_DYNAMIC:
        # For dynamic actual set we filter; would_add still uses full eligible vs pin.
        pass
    return compare_discovery_sets(
        mode=mode,
        pinned_ids=pinned,
        dynamic_eligible_ids=dynamic,
        cohort_ids=cohort if normalize_discovery_mode(mode) == MODE_DYNAMIC else [],
        classifications=classifications,
    )
