"""L8 — Controlled promotion of proven LEGACY_UNCLASSIFIED Rubika sessions.

Never sets ACTIVE directly. Sequence:

  LEGACY_UNCLASSIFIED
  → fresh decrypt / structure / authenticated reconnect / identity prove
  → Account identity bind/verify (no silent overwrite)
  → mark VALIDATING
  → promote_validated_session(...) → ACTIVE

Rollback is exact-ID guarded and restores identity only if this operation newly bound it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    Campaign,
    CampaignAccount,
    CampaignStatus,
    ChannelSession,
    RubikaIdentityStatus,
    RubikaSessionStatus,
    SessionType,
)
from core_engine.services.crypto import SessionDecryptionError
from core_engine.services.rubika_candidate_prover import (
    PROOF_PASS,
    CandidateProofResult,
    RealRubikaCandidateProver,
)
from core_engine.services.rubika_canonical_session import (
    CanonicalSessionError,
    load_canonical_rubika_session,
)
from core_engine.services.rubika_identity import (
    IDENTITY_MISMATCH,
    bind_or_verify_rubika_identity,
)
from core_engine.services.rubika_session_promotion import (
    PROMOTION_OK,
    promote_validated_session,
)
from core_engine.services.rubika_user_session import parse_session_envelope
from core_engine.services.session_storage import load_channel_session_plaintext

logger = logging.getLogger("core_engine.services.rubika_legacy_promotion")

# Gate / result codes
GATE_OK = "GATE_OK"
SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
SESSION_WRONG_ACCOUNT = "SESSION_WRONG_ACCOUNT"
SESSION_WRONG_STATUS = "SESSION_WRONG_STATUS"
SESSION_WRONG_TYPE = "SESSION_WRONG_TYPE"
ACTIVE_ALREADY_EXISTS = "ACTIVE_ALREADY_EXISTS"
DECRYPT_FAILED = "SESSION_DECRYPT_FAILED"
STRUCTURE_INVALID = "SESSION_STRUCTURALLY_INVALID"
RECONNECT_FAILED = "AUTH_RECONNECT_FAILED"
IDENTITY_EXPECTED_MISMATCH = "IDENTITY_EXPECTED_MISMATCH"
IDENTITY_CONFLICT = "IDENTITY_MISMATCH"
QUEUE_NOT_EMPTY = "QUEUE_NOT_EMPTY"
CAMPAIGN_RUNNING = "CAMPAIGN_RUNNING"
ACCOUNT_MISSING = "ACCOUNT_MISSING"
PROOF_REQUIRED = "PROOF_REQUIRED"
PROMOTION_FAILED = "PROMOTION_FAILED"
ROLLBACK_OK = "ROLLBACK_OK"
ROLLBACK_TARGET_NOT_ACTIVE = "ROLLBACK_TARGET_NOT_ACTIVE"
ROLLBACK_WRONG_ACCOUNT = "ROLLBACK_WRONG_ACCOUNT"

_RUNNING_CAMPAIGN_STATUSES = frozenset(
    {
        CampaignStatus.RUNNING,
        # string form for DB rows stored as text
    }
)


class SessionProver(Protocol):
    async def prove_detailed(
        self,
        db: Session,
        *,
        account_id: int,
        session_id: int,
        expected_guid: str | None,
    ) -> CandidateProofResult: ...


@dataclass(frozen=True)
class LegacyPromotionEvidence:
    """Operator attestation metadata — never a proof bypass."""

    source_phase: str = "L8"
    operator_note: str = ""
    prior_probe_reference: str | None = None


@dataclass
class IdentitySnapshot:
    rubika_guid: str | None
    rubika_identity_status: RubikaIdentityStatus | None
    rubika_identity_verified_at: datetime | None
    newly_bound: bool = False


@dataclass
class LegacyPromotionResult:
    ok: bool
    code: str
    account_id: int
    session_id: int
    active_session_id: int | None = None
    proven_identity_guid: str | None = None
    identity_newly_bound: bool = False
    identity_snapshot_before: IdentitySnapshot | None = None
    sanitized_message: str = ""
    proof_duration_ms: int | None = None

    def as_safe_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "account_id": self.account_id,
            "session_id": self.session_id,
            "active_session_id": self.active_session_id,
            "proven_identity_guid": self.proven_identity_guid,
            "identity_newly_bound": self.identity_newly_bound,
            "sanitized_message": self.sanitized_message,
            "proof_duration_ms": self.proof_duration_ms,
        }


@dataclass
class RollbackResult:
    ok: bool
    code: str
    account_id: int
    session_id: int
    identity_restored: bool = False


def _fail(
    *,
    account_id: int,
    session_id: int,
    code: str,
    message: str = "",
    snapshot: IdentitySnapshot | None = None,
) -> LegacyPromotionResult:
    return LegacyPromotionResult(
        ok=False,
        code=code,
        account_id=int(account_id),
        session_id=int(session_id),
        sanitized_message=message[:200],
        identity_snapshot_before=snapshot,
    )


def _snapshot_identity(account: Account) -> IdentitySnapshot:
    return IdentitySnapshot(
        rubika_guid=str(account.rubika_guid or "").strip() or None,
        rubika_identity_status=account.rubika_identity_status,
        rubika_identity_verified_at=account.rubika_identity_verified_at,
        newly_bound=False,
    )


def assert_no_running_campaign_for_account(db: Session, account_id: int) -> str | None:
    """Return error code if a running campaign uses this account."""
    rows = (
        db.query(Campaign.id, Campaign.status)
        .join(CampaignAccount, CampaignAccount.campaign_id == Campaign.id)
        .filter(CampaignAccount.account_id == int(account_id))
        .all()
    )
    for _cid, status in rows:
        st = status.value if hasattr(status, "value") else str(status)
        if st == CampaignStatus.RUNNING.value or status == CampaignStatus.RUNNING:
            return CAMPAIGN_RUNNING
    return None


def assert_queue_empty(
    account_id: int,
    *,
    queue_length_fn: Callable[[int], int] | None,
) -> str | None:
    if queue_length_fn is None:
        return None
    try:
        length = int(queue_length_fn(int(account_id)))
    except Exception as exc:  # noqa: BLE001
        return f"{QUEUE_NOT_EMPTY}:{type(exc).__name__}"
    if length != 0:
        return QUEUE_NOT_EMPTY
    return None


def _load_exact_legacy_row(
    db: Session, *, account_id: int, session_id: int
) -> tuple[ChannelSession | None, Account | None, str | None]:
    account = db.query(Account).filter(Account.id == int(account_id)).first()
    if account is None:
        return None, None, ACCOUNT_MISSING
    row = (
        db.query(ChannelSession)
        .filter(ChannelSession.id == int(session_id))
        .with_for_update()
        .first()
    )
    if row is None:
        return None, account, SESSION_NOT_FOUND
    if int(row.account_id) != int(account_id):
        return row, account, SESSION_WRONG_ACCOUNT
    if row.session_type != SessionType.RUBIKA_SESSION:
        return row, account, SESSION_WRONG_TYPE
    if row.session_status != RubikaSessionStatus.LEGACY_UNCLASSIFIED:
        return row, account, SESSION_WRONG_STATUS
    active_n = (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == int(account_id),
            ChannelSession.session_type == SessionType.RUBIKA_SESSION,
            ChannelSession.session_status == RubikaSessionStatus.ACTIVE,
        )
        .count()
    )
    if active_n > 0:
        return row, account, ACTIVE_ALREADY_EXISTS
    return row, account, None


def _identity_conflict_without_mutate(account: Account, proven_guid: str) -> bool:
    existing = str(account.rubika_guid or "").strip() or None
    status = account.rubika_identity_status
    if existing is None or status in {None, RubikaIdentityStatus.UNBOUND}:
        return False
    return existing != proven_guid


async def promote_proven_legacy_rubika_session(
    db: Session,
    *,
    account_id: int,
    session_id: int,
    expected_identity: str | None,
    evidence: LegacyPromotionEvidence,
    prover: SessionProver | None = None,
    queue_length_fn: Callable[[int], int] | None = None,
    skip_network_proof: bool = False,
    injected_proof: CandidateProofResult | None = None,
) -> LegacyPromotionResult:
    """Promote one LEGACY_UNCLASSIFIED session after fresh proof.

    ``skip_network_proof`` + ``injected_proof`` are test-only hooks.
    Production callers must leave them at defaults (fresh RealRubikaCandidateProver).
    """
    _ = evidence  # required parameter; never bypasses proof
    aid = int(account_id)
    sid = int(session_id)

    camp_err = assert_no_running_campaign_for_account(db, aid)
    if camp_err:
        return _fail(account_id=aid, session_id=sid, code=camp_err)

    q_err = assert_queue_empty(aid, queue_length_fn=queue_length_fn)
    if q_err:
        return _fail(account_id=aid, session_id=sid, code=q_err)

    row, account, gate = _load_exact_legacy_row(db, account_id=aid, session_id=sid)
    if gate or row is None or account is None:
        return _fail(account_id=aid, session_id=sid, code=gate or SESSION_NOT_FOUND)

    snapshot = _snapshot_identity(account)

    # Local decrypt + structure (independent of prover) — refuse without ciphertext.
    try:
        plaintext = load_channel_session_plaintext(row)
    except (SessionDecryptionError, Exception) as exc:  # noqa: BLE001
        return _fail(
            account_id=aid,
            session_id=sid,
            code=DECRYPT_FAILED,
            message=type(exc).__name__,
            snapshot=snapshot,
        )
    try:
        parse_session_envelope(plaintext)
    except ValueError as exc:
        return _fail(
            account_id=aid,
            session_id=sid,
            code=STRUCTURE_INVALID,
            message=str(exc)[:120],
            snapshot=snapshot,
        )

    # Fresh authenticated reconnect + identity (required unless test injects proof).
    if skip_network_proof:
        if injected_proof is None or not injected_proof.ok:
            return _fail(
                account_id=aid,
                session_id=sid,
                code=PROOF_REQUIRED,
                message="test injected proof missing or not ok",
                snapshot=snapshot,
            )
        proof = injected_proof
    else:
        net_prover: SessionProver = prover or RealRubikaCandidateProver()
        proof = await net_prover.prove_detailed(
            db,
            account_id=aid,
            session_id=sid,
            expected_guid=expected_identity,
        )
        if not proof.ok or proof.proof_status != PROOF_PASS:
            code = proof.error_code or proof.proof_status or RECONNECT_FAILED
            if code == IDENTITY_MISMATCH:
                return _fail(
                    account_id=aid,
                    session_id=sid,
                    code=IDENTITY_CONFLICT,
                    message=proof.sanitized_message,
                    snapshot=snapshot,
                )
            return _fail(
                account_id=aid,
                session_id=sid,
                code=code if code else RECONNECT_FAILED,
                message=proof.sanitized_message,
                snapshot=snapshot,
            )

    proven_guid = str(proof.identity_guid or "").strip() or None
    if not proven_guid:
        return _fail(
            account_id=aid,
            session_id=sid,
            code=RECONNECT_FAILED,
            message="proven identity guid missing",
            snapshot=snapshot,
        )

    if expected_identity is not None:
        expected = str(expected_identity).strip()
        if expected and expected != proven_guid:
            return _fail(
                account_id=aid,
                session_id=sid,
                code=IDENTITY_EXPECTED_MISMATCH,
                message="proven guid != expected_identity",
                snapshot=snapshot,
            )

    if _identity_conflict_without_mutate(account, proven_guid):
        return _fail(
            account_id=aid,
            session_id=sid,
            code=IDENTITY_CONFLICT,
            message="Account.rubika_guid differs from proven guid",
            snapshot=snapshot,
        )

    # Bind/verify on Account (no silent overwrite — bind_or_verify enforces).
    now = datetime.now(timezone.utc)
    identity = bind_or_verify_rubika_identity(
        account,
        proven_guid,
        allow_bind_if_unbound=True,
        clock=now,
    )
    if not identity.ok:
        db.flush()
        return _fail(
            account_id=aid,
            session_id=sid,
            code=identity.code,
            message="identity bind/verify failed",
            snapshot=snapshot,
        )
    newly_bound = bool(identity.newly_bound)
    snapshot.newly_bound = newly_bound

    # Mark VALIDATING then use existing atomic promote (VALIDATING → ACTIVE).
    row.session_status = RubikaSessionStatus.VALIDATING
    row.identity_guid = proven_guid
    row.validation_error_code = None
    db.flush()

    promo = promote_validated_session(
        db,
        account_id=aid,
        candidate_session_id=sid,
        clock=now,
        bind_identity_if_unbound=True,
    )
    if not promo.ok or promo.code != PROMOTION_OK:
        # Leave row not ACTIVE — revert VALIDATING → LEGACY for cleanliness.
        db.refresh(row)
        if row.session_status == RubikaSessionStatus.VALIDATING:
            row.session_status = RubikaSessionStatus.LEGACY_UNCLASSIFIED
        if newly_bound:
            account.rubika_guid = snapshot.rubika_guid
            account.rubika_identity_status = (
                snapshot.rubika_identity_status or RubikaIdentityStatus.UNBOUND
            )
            account.rubika_identity_verified_at = snapshot.rubika_identity_verified_at
        db.flush()
        return _fail(
            account_id=aid,
            session_id=sid,
            code=promo.code or PROMOTION_FAILED,
            message="promote_validated_session refused",
            snapshot=snapshot,
        )

    db.commit()
    logger.info(
        "event=legacy_session_promoted account_id=%s session_id=%s "
        "newly_bound=%s evidence_phase=%s",
        aid,
        sid,
        newly_bound,
        evidence.source_phase,
    )
    return LegacyPromotionResult(
        ok=True,
        code=PROMOTION_OK,
        account_id=aid,
        session_id=sid,
        active_session_id=int(promo.active_session_id or sid),
        proven_identity_guid=proven_guid,
        identity_newly_bound=newly_bound,
        identity_snapshot_before=snapshot,
        sanitized_message="legacy session promoted via validating path",
        proof_duration_ms=proof.duration_ms,
    )


def rollback_legacy_promotion(
    db: Session,
    *,
    account_id: int,
    session_id: int,
    identity_snapshot_before: IdentitySnapshot | None,
) -> RollbackResult:
    """Exact-ID rollback: ACTIVE target → LEGACY_UNCLASSIFIED; restore identity if newly bound."""
    aid = int(account_id)
    sid = int(session_id)
    row = (
        db.query(ChannelSession)
        .filter(ChannelSession.id == sid)
        .with_for_update()
        .first()
    )
    if row is None:
        return RollbackResult(ok=False, code=SESSION_NOT_FOUND, account_id=aid, session_id=sid)
    if int(row.account_id) != aid:
        return RollbackResult(
            ok=False, code=ROLLBACK_WRONG_ACCOUNT, account_id=aid, session_id=sid
        )
    if row.session_status != RubikaSessionStatus.ACTIVE:
        return RollbackResult(
            ok=False, code=ROLLBACK_TARGET_NOT_ACTIVE, account_id=aid, session_id=sid
        )

    row.session_status = RubikaSessionStatus.LEGACY_UNCLASSIFIED
    row.validated_at = None
    # Keep identity_guid on session row (non-secret binding hint); status is legacy again.

    identity_restored = False
    if identity_snapshot_before is not None and identity_snapshot_before.newly_bound:
        account = db.query(Account).filter(Account.id == aid).with_for_update().first()
        if account is not None:
            account.rubika_guid = identity_snapshot_before.rubika_guid
            account.rubika_identity_status = (
                identity_snapshot_before.rubika_identity_status
                or RubikaIdentityStatus.UNBOUND
            )
            account.rubika_identity_verified_at = (
                identity_snapshot_before.rubika_identity_verified_at
            )
            identity_restored = True

    db.commit()
    logger.info(
        "event=legacy_promotion_rolled_back account_id=%s session_id=%s "
        "identity_restored=%s",
        aid,
        sid,
        identity_restored,
    )
    return RollbackResult(
        ok=True,
        code=ROLLBACK_OK,
        account_id=aid,
        session_id=sid,
        identity_restored=identity_restored,
    )


def classify_legacy_decrypt_failed_session(
    db: Session,
    *,
    account_id: int,
    session_id: int,
    require_decrypt_failure: bool = True,
    clock: datetime | None = None,
) -> dict[str, Any]:
    """Classify a non-ACTIVE legacy sibling as DECRYPT_FAILED (evidence retained).

    Never deletes. Never marks SUPERSEDED (that status is for prior ACTIVE only).
    Never touches ACTIVE rows. Used when a sibling cannot decrypt and must not
    remain runtime-ambiguous as LEGACY_UNCLASSIFIED after a proven promote.
    """
    now = clock or datetime.now(timezone.utc)
    aid = int(account_id)
    sid = int(session_id)
    row = (
        db.query(ChannelSession)
        .filter(ChannelSession.id == sid)
        .with_for_update()
        .first()
    )
    if row is None:
        return {"ok": False, "code": SESSION_NOT_FOUND, "session_id": sid}
    if int(row.account_id) != aid:
        return {"ok": False, "code": SESSION_WRONG_ACCOUNT, "session_id": sid}
    if row.session_type != SessionType.RUBIKA_SESSION:
        return {"ok": False, "code": SESSION_WRONG_TYPE, "session_id": sid}
    if row.session_status == RubikaSessionStatus.ACTIVE:
        return {"ok": False, "code": "REFUSE_CLASSIFY_ACTIVE", "session_id": sid}
    if row.session_status not in {
        RubikaSessionStatus.LEGACY_UNCLASSIFIED,
        RubikaSessionStatus.DECRYPT_FAILED,
    }:
        return {
            "ok": False,
            "code": SESSION_WRONG_STATUS,
            "session_id": sid,
            "status": row.session_status.value if row.session_status else None,
        }

    if require_decrypt_failure:
        try:
            load_channel_session_plaintext(row)
            return {"ok": False, "code": "DECRYPT_UNEXPECTEDLY_OK", "session_id": sid}
        except (SessionDecryptionError, Exception):  # noqa: BLE001
            pass

    prior = row.session_status.value if row.session_status else None
    row.session_status = RubikaSessionStatus.DECRYPT_FAILED
    row.validation_error_code = DECRYPT_FAILED
    row.invalidated_at = now
    db.flush()
    return {
        "ok": True,
        "code": "CLASSIFIED_DECRYPT_FAILED",
        "session_id": sid,
        "prior_status": prior,
        "post_status": RubikaSessionStatus.DECRYPT_FAILED.value,
    }


def rollback_decrypt_failed_classification(
    db: Session,
    *,
    account_id: int,
    session_id: int,
    prior_status: str | None = "legacy_unclassified",
) -> dict[str, Any]:
    """Restore a DECRYPT_FAILED sibling to its pre-classification status (usually legacy)."""
    aid = int(account_id)
    sid = int(session_id)
    row = (
        db.query(ChannelSession)
        .filter(ChannelSession.id == sid)
        .with_for_update()
        .first()
    )
    if row is None:
        return {"ok": False, "code": SESSION_NOT_FOUND, "session_id": sid}
    if int(row.account_id) != aid:
        return {"ok": False, "code": SESSION_WRONG_ACCOUNT, "session_id": sid}
    if row.session_status != RubikaSessionStatus.DECRYPT_FAILED:
        return {
            "ok": False,
            "code": "NOT_DECRYPT_FAILED",
            "session_id": sid,
            "status": row.session_status.value if row.session_status else None,
        }
    restore = prior_status or RubikaSessionStatus.LEGACY_UNCLASSIFIED.value
    try:
        row.session_status = RubikaSessionStatus(restore)
    except ValueError:
        row.session_status = RubikaSessionStatus.LEGACY_UNCLASSIFIED
    row.validation_error_code = None
    row.invalidated_at = None
    db.flush()
    return {
        "ok": True,
        "code": ROLLBACK_OK,
        "session_id": sid,
        "restored_status": row.session_status.value,
    }


def verify_post_promotion(
    db: Session,
    *,
    account_id: int,
    session_id: int,
) -> dict[str, Any]:
    """Post-promotion checks (no send / no OTP)."""
    aid = int(account_id)
    sid = int(session_id)
    active = (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == aid,
            ChannelSession.session_type == SessionType.RUBIKA_SESSION,
            ChannelSession.session_status == RubikaSessionStatus.ACTIVE,
        )
        .all()
    )
    from core_engine.services.rubika_canonical_runtime import (
        select_legacy_rubika_session_row,
    )

    legacy = select_legacy_rubika_session_row(db, aid)
    out: dict[str, Any] = {
        "account_id": aid,
        "expected_session_id": sid,
        "active_count": len(active),
        "active_session_ids": [int(r.id) for r in active],
        "legacy_selected_session_id": int(legacy.id) if legacy else None,
        "canonical_ok": False,
        "canonical_session_id": None,
        "canonical_error": None,
        "legacy_canonical_match": False,
    }
    if len(active) != 1 or int(active[0].id) != sid:
        return out
    try:
        loaded = load_canonical_rubika_session(
            db, aid, require_identity_binding=True, return_plaintext=False
        )
        out["canonical_ok"] = True
        out["canonical_session_id"] = int(loaded.session_id)
    except CanonicalSessionError as exc:
        out["canonical_error"] = exc.code
        return out
    out["legacy_canonical_match"] = (
        out["legacy_selected_session_id"] == out["canonical_session_id"] == sid
    )
    return out
