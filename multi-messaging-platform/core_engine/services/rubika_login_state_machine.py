"""Rubika OTP/login state machine (L3).

Authoritative entry points:
  - request_rubika_login
  - submit_rubika_login_code

OTP codes are never persisted. Provider secrets live only in the injected
provider / ephemeral store — never in challenge rows as plaintext OTP.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    PlatformType,
    RubikaLoginChallenge,
    RubikaLoginChallengeState,
    RubikaSessionStatus,
    SessionType,
)
from core_engine.services.rubika_canonical_session import (
    CanonicalSessionError,
    load_canonical_rubika_session,
)
from core_engine.services.rubika_identity import IDENTITY_MISMATCH
from core_engine.services.rubika_session_promotion import (
    PROMOTION_OK,
    promote_validated_session,
)
from core_engine.services.rubika_user_session import (
    build_session_envelope,
    parse_session_envelope,
)
from core_engine.services.session_storage import store_channel_session

logger = logging.getLogger("core_engine.services.rubika_login_state_machine")

ACTIVE_CHALLENGE_STATES = frozenset(
    {
        RubikaLoginChallengeState.OTP_REQUESTED,
        RubikaLoginChallengeState.OTP_WAITING_FOR_OPERATOR,
        RubikaLoginChallengeState.OTP_SUBMITTED,
        RubikaLoginChallengeState.AUTHENTICATING,
        RubikaLoginChallengeState.IDENTITY_VERIFYING,
        RubikaLoginChallengeState.SESSION_PERSISTING,
    }
)

# Result / failure codes
OTP_REQUESTED = "OTP_REQUESTED"
OTP_ALREADY_PENDING = "OTP_ALREADY_PENDING"
OTP_RATE_LIMITED = "OTP_RATE_LIMITED"
OTP_REQUEST_FAILED = "OTP_REQUEST_FAILED"
ACCOUNT_NOT_FOUND = "ACCOUNT_NOT_FOUND"
WRONG_PLATFORM = "WRONG_PLATFORM"
OTP_INVALID = "OTP_INVALID"
OTP_EXPIRED = "OTP_EXPIRED"
CHALLENGE_NOT_FOUND = "CHALLENGE_NOT_FOUND"
CHALLENGE_WRONG_ACCOUNT = "CHALLENGE_WRONG_ACCOUNT"
CHALLENGE_NOT_ACTIVE = "CHALLENGE_NOT_ACTIVE"
LOGIN_ALREADY_COMPLETED = "LOGIN_ALREADY_COMPLETED"
SESSION_STRUCTURE_INVALID = "SESSION_STRUCTURE_INVALID"
SESSION_STRUCTURALLY_INVALID = "SESSION_STRUCTURALLY_INVALID"
AUTH_RECONNECT_TIMEOUT = "AUTH_RECONNECT_TIMEOUT"
IDENTITY_MISSING = "IDENTITY_MISSING"
PROVIDER_ERROR = "PROVIDER_ERROR"
AUTH_RECONNECT_FAILED = "AUTH_RECONNECT_FAILED"
SESSION_PROMOTION_FAILED = "SESSION_PROMOTION_FAILED"
LOGIN_READY = "LOGIN_READY"
LEGACY_LOGIN_DISABLED = "LEGACY_LOGIN_DISABLED_USE_STATE_MACHINE"


@dataclass
class ProviderOtpRequestResult:
    ok: bool
    code: str
    provider_challenge_id: str | None = None
    # Ephemeral secrets for later submit — never OTP code; never written to DB as OTP.
    secret_blob: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass
class ProviderOtpSubmitResult:
    ok: bool
    code: str
    phone_number: str | None = None
    auth: str | None = None
    guid: str | None = None
    user_agent: str | None = None
    private_key: str | None = None
    message: str = ""


class RubikaLoginProvider(Protocol):
    async def request_otp(self, *, phone_e164: str, account_id: int) -> ProviderOtpRequestResult: ...

    async def submit_otp(
        self,
        *,
        phone_e164: str,
        provider_challenge_id: str | None,
        secret_blob: dict[str, Any],
        code: str,
    ) -> ProviderOtpSubmitResult: ...


@dataclass
class CandidateProveResult:
    ok: bool
    code: str
    identity_guid: str | None = None


class CandidateSessionProver(Protocol):
    async def prove(
        self,
        db: Session,
        *,
        account_id: int,
        session_id: int,
        expected_guid: str | None,
    ) -> CandidateProveResult: ...


@dataclass(frozen=True)
class LoginRequestResult:
    ok: bool
    code: str
    challenge_id: str | None = None
    state: str | None = None
    expires_at: str | None = None
    message: str = ""


@dataclass(frozen=True)
class LoginSubmitResult:
    ok: bool
    code: str
    challenge_id: str | None = None
    state: str | None = None
    login_state: str | None = None
    auth_ready: bool | None = None
    dispatch_ready: bool | None = None
    dispatch_block: str | None = None
    active_session_id: int | None = None
    message: str = ""


def _now(clock: datetime | None) -> datetime:
    return clock or datetime.now(timezone.utc)


def _normalize_phone(phone: str) -> str:
    p = phone.strip()
    if p.startswith("0"):
        return f"98{p[1:]}"
    return p


def _get_active_challenge(db: Session, account_id: int) -> RubikaLoginChallenge | None:
    return (
        db.query(RubikaLoginChallenge)
        .filter(
            RubikaLoginChallenge.account_id == int(account_id),
            RubikaLoginChallenge.state.in_(list(ACTIVE_CHALLENGE_STATES)),
        )
        .order_by(RubikaLoginChallenge.created_at.desc())
        .with_for_update()
        .first()
    )


def evaluate_dispatch_ready_readonly(db: Session, account_id: int) -> tuple[bool, str | None]:
    """Non-mutating dispatch readiness (pool/schedule/coverage not auto-fixed)."""
    settings = get_settings()
    # Login success must not imply pool enrollment.
    if not bool(getattr(settings, "AUTO_ENROLL_RUBIKA_POOL", False)):
        # Still report whether pool membership exists — without creating it.
        from core_engine.models import RubikaAccountPool
        from workers.rubika_account_pool import resolve_current_phase

        phase = resolve_current_phase(db)
        if phase is None:
            return False, "NO_SCHEDULE"
        row = (
            db.query(RubikaAccountPool)
            .filter(
                RubikaAccountPool.account_id == int(account_id),
                RubikaAccountPool.phase == phase,
            )
            .first()
        )
        if row is None:
            return False, "NOT_IN_POOL"
        return False, "NO_WORKER_CONSUMER"  # coverage not claimed during login
    return False, "NO_WORKER_CONSUMER"


def evaluate_auth_ready(db: Session, account_id: int) -> bool:
    try:
        load_canonical_rubika_session(db, account_id, require_identity_binding=True)
        return True
    except CanonicalSessionError:
        return False


async def request_rubika_login(
    db: Session,
    account_id: int,
    *,
    phone_number: str | None = None,
    provider: RubikaLoginProvider,
    clock: datetime | None = None,
    secret_store: dict[str, dict[str, Any]] | None = None,
) -> LoginRequestResult:
    """Authoritative OTP request — idempotent per account."""
    now = _now(clock)
    settings = get_settings()
    ttl = int(getattr(settings, "RUBIKA_OTP_CHALLENGE_TTL_SECONDS", 600) or 600)
    cooldown = int(getattr(settings, "RUBIKA_OTP_RESEND_COOLDOWN_SECONDS", 60) or 60)

    account = db.query(Account).filter(Account.id == int(account_id)).first()
    if account is None:
        return LoginRequestResult(ok=False, code=ACCOUNT_NOT_FOUND)
    if account.platform != PlatformType.RUBIKA:
        return LoginRequestResult(ok=False, code=WRONG_PLATFORM)

    active = _get_active_challenge(db, account_id)
    if active is not None:
        if active.expires_at and active.expires_at <= now:
            active.state = RubikaLoginChallengeState.LOGIN_EXPIRED
            active.failure_code = OTP_EXPIRED
            db.flush()
        else:
            return LoginRequestResult(
                ok=False,
                code=OTP_ALREADY_PENDING,
                challenge_id=active.id,
                state=active.state.value,
                expires_at=active.expires_at.isoformat() if active.expires_at else None,
                message="An active login challenge already exists for this account.",
            )

    # Resend cooldown from last request timestamp (any prior challenge).
    last = (
        db.query(RubikaLoginChallenge)
        .filter(RubikaLoginChallenge.account_id == int(account_id))
        .order_by(RubikaLoginChallenge.requested_at.desc())
        .first()
    )
    if last and last.requested_at is not None:
        elapsed = (now - last.requested_at).total_seconds()
        if elapsed < cooldown:
            return LoginRequestResult(ok=False, code=OTP_RATE_LIMITED)

    phone = _normalize_phone(phone_number or account.phone_number or "")
    if not phone:
        return LoginRequestResult(ok=False, code=OTP_REQUEST_FAILED, message="phone required")

    challenge_id = uuid.uuid4().hex
    ch = RubikaLoginChallenge(
        id=challenge_id,
        account_id=int(account_id),
        state=RubikaLoginChallengeState.OTP_REQUESTED,
        phone_e164=phone,
        requested_at=now,
        expires_at=now + timedelta(seconds=ttl),
        attempt_count=0,
    )
    db.add(ch)
    db.flush()

    try:
        prov = await provider.request_otp(phone_e164=phone, account_id=int(account_id))
    except Exception:  # noqa: BLE001
        ch.state = RubikaLoginChallengeState.LOGIN_FAILED
        ch.failure_code = OTP_REQUEST_FAILED
        db.flush()
        return LoginRequestResult(ok=False, code=OTP_REQUEST_FAILED, challenge_id=challenge_id)

    if not prov.ok:
        ch.state = RubikaLoginChallengeState.LOGIN_FAILED
        ch.failure_code = prov.code or OTP_REQUEST_FAILED
        db.flush()
        return LoginRequestResult(
            ok=False,
            code=prov.code or OTP_REQUEST_FAILED,
            challenge_id=challenge_id,
        )

    ch.provider_challenge_id = prov.provider_challenge_id
    ch.state = RubikaLoginChallengeState.OTP_WAITING_FOR_OPERATOR
    db.flush()

    store = secret_store if secret_store is not None else _DEFAULT_SECRET_STORE
    # Never store OTP code — only provider handshake secrets.
    store[challenge_id] = dict(prov.secret_blob or {})

    return LoginRequestResult(
        ok=True,
        code=OTP_REQUESTED,
        challenge_id=challenge_id,
        state=ch.state.value,
        expires_at=ch.expires_at.isoformat() if ch.expires_at else None,
        message="OTP challenge pending operator code.",
    )


# Process-local fallback for tests / single-process; live deployments should inject Redis-backed store.
_DEFAULT_SECRET_STORE: dict[str, dict[str, Any]] = {}


async def submit_rubika_login_code(
    db: Session,
    account_id: int,
    challenge_id: str,
    code: str,
    *,
    provider: RubikaLoginProvider,
    prover: CandidateSessionProver,
    clock: datetime | None = None,
    secret_store: dict[str, dict[str, Any]] | None = None,
) -> LoginSubmitResult:
    """Authoritative OTP submit → prove → promote. READY only after auth gates."""
    now = _now(clock)
    store = secret_store if secret_store is not None else _DEFAULT_SECRET_STORE

    account = db.query(Account).filter(Account.id == int(account_id)).first()
    if account is None:
        return LoginSubmitResult(ok=False, code=ACCOUNT_NOT_FOUND)
    if account.platform != PlatformType.RUBIKA:
        return LoginSubmitResult(ok=False, code=WRONG_PLATFORM)

    ch = (
        db.query(RubikaLoginChallenge)
        .filter(RubikaLoginChallenge.id == str(challenge_id))
        .with_for_update()
        .first()
    )
    if ch is None:
        return LoginSubmitResult(ok=False, code=CHALLENGE_NOT_FOUND)
    if int(ch.account_id) != int(account_id):
        return LoginSubmitResult(ok=False, code=CHALLENGE_WRONG_ACCOUNT)

    if ch.state == RubikaLoginChallengeState.READY and ch.completed_session_id:
        return LoginSubmitResult(
            ok=True,
            code=LOGIN_ALREADY_COMPLETED,
            challenge_id=ch.id,
            state=ch.state.value,
            login_state=ch.state.value,
            auth_ready=evaluate_auth_ready(db, account_id),
            dispatch_ready=False,
            dispatch_block="IDEMPOTENT_REPLAY",
            active_session_id=ch.completed_session_id,
            message="Login already completed for this challenge.",
        )

    if ch.state not in ACTIVE_CHALLENGE_STATES and ch.state != RubikaLoginChallengeState.OTP_WAITING_FOR_OPERATOR:
        if ch.state == RubikaLoginChallengeState.OTP_WAITING_FOR_OPERATOR:
            pass
        else:
            return LoginSubmitResult(
                ok=False,
                code=CHALLENGE_NOT_ACTIVE,
                challenge_id=ch.id,
                state=ch.state.value,
            )

    if ch.expires_at and ch.expires_at <= now:
        ch.state = RubikaLoginChallengeState.LOGIN_EXPIRED
        ch.failure_code = OTP_EXPIRED
        db.flush()
        return LoginSubmitResult(ok=False, code=OTP_EXPIRED, challenge_id=ch.id, state=ch.state.value)

    if ch.state != RubikaLoginChallengeState.OTP_WAITING_FOR_OPERATOR:
        # In-flight processing — do not start a second candidate.
        if ch.state in {
            RubikaLoginChallengeState.OTP_SUBMITTED,
            RubikaLoginChallengeState.AUTHENTICATING,
            RubikaLoginChallengeState.IDENTITY_VERIFYING,
            RubikaLoginChallengeState.SESSION_PERSISTING,
        }:
            return LoginSubmitResult(
                ok=False,
                code=OTP_ALREADY_PENDING,
                challenge_id=ch.id,
                state=ch.state.value,
                message="Challenge already processing.",
            )
        return LoginSubmitResult(
            ok=False,
            code=CHALLENGE_NOT_ACTIVE,
            challenge_id=ch.id,
            state=ch.state.value,
        )

    otp = (code or "").strip()
    if not otp:
        return LoginSubmitResult(ok=False, code=OTP_INVALID, challenge_id=ch.id)

    ch.state = RubikaLoginChallengeState.OTP_SUBMITTED
    ch.last_submit_at = now
    ch.attempt_count = int(ch.attempt_count or 0) + 1
    db.flush()

    secret_blob = store.get(ch.id) or {}
    ch.state = RubikaLoginChallengeState.AUTHENTICATING
    db.flush()

    try:
        auth_result = await provider.submit_otp(
            phone_e164=str(ch.phone_e164 or ""),
            provider_challenge_id=ch.provider_challenge_id,
            secret_blob=secret_blob,
            code=otp,
        )
    except Exception:  # noqa: BLE001
        ch.state = RubikaLoginChallengeState.LOGIN_FAILED
        ch.failure_code = OTP_INVALID
        db.flush()
        return LoginSubmitResult(ok=False, code=OTP_INVALID, challenge_id=ch.id, state=ch.state.value)

    if not auth_result.ok:
        ch.state = RubikaLoginChallengeState.LOGIN_FAILED
        ch.failure_code = auth_result.code or OTP_INVALID
        db.flush()
        return LoginSubmitResult(
            ok=False,
            code=auth_result.code or OTP_INVALID,
            challenge_id=ch.id,
            state=ch.state.value,
        )

    # Build envelope in memory — never log secrets.
    try:
        envelope = build_session_envelope(
            phone_number=str(auth_result.phone_number or ch.phone_e164),
            auth=str(auth_result.auth),
            guid=str(auth_result.guid),
            user_agent=str(auth_result.user_agent or "rubika-l3"),
            private_key=str(auth_result.private_key),
        )
        parse_session_envelope(envelope)
    except Exception:  # noqa: BLE001
        ch.state = RubikaLoginChallengeState.LOGIN_FAILED
        ch.failure_code = SESSION_STRUCTURE_INVALID
        db.flush()
        return LoginSubmitResult(
            ok=False,
            code=SESSION_STRUCTURE_INVALID,
            challenge_id=ch.id,
            state=ch.state.value,
        )

    # Persist candidate as VALIDATING — old ACTIVE untouched.
    candidate = store_channel_session(
        db,
        account_id=int(account_id),
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=envelope,
        session_status=RubikaSessionStatus.VALIDATING,
        identity_guid=str(auth_result.guid),
        login_attempt_id=ch.id,
    )
    ch.candidate_session_id = int(candidate.id)
    ch.state = RubikaLoginChallengeState.IDENTITY_VERIFYING
    db.flush()

    from core_engine.services.rubika_candidate_prover import assert_prover_allowed_for_canonical

    assert_prover_allowed_for_canonical(prover)

    prove = await prover.prove(
        db,
        account_id=int(account_id),
        session_id=int(candidate.id),
        expected_guid=str(auth_result.guid),
    )
    if not prove.ok:
        candidate.session_status = (
            RubikaSessionStatus.INVALID
            if prove.code != "SESSION_DECRYPT_FAILED"
            else RubikaSessionStatus.DECRYPT_FAILED
        )
        candidate.validation_error_code = prove.code
        candidate.invalidated_at = now
        if prove.code == IDENTITY_MISMATCH:
            ch.state = RubikaLoginChallengeState.MANUAL_REVIEW_REQUIRED
            ch.failure_code = IDENTITY_MISMATCH
        else:
            ch.state = RubikaLoginChallengeState.LOGIN_FAILED
            ch.failure_code = prove.code or AUTH_RECONNECT_FAILED
        db.flush()
        return LoginSubmitResult(
            ok=False,
            code=prove.code or AUTH_RECONNECT_FAILED,
            challenge_id=ch.id,
            state=ch.state.value,
            auth_ready=False,
            dispatch_ready=False,
        )

    ch.state = RubikaLoginChallengeState.SESSION_PERSISTING
    db.flush()

    promo = promote_validated_session(
        db,
        account_id=int(account_id),
        candidate_session_id=int(candidate.id),
        clock=now,
    )
    if not promo.ok:
        # Leave candidate VALIDATING/invalid; never ACTIVE. Old ACTIVE untouched by promote.
        if candidate.session_status == RubikaSessionStatus.VALIDATING:
            candidate.session_status = RubikaSessionStatus.INVALID
            candidate.validation_error_code = promo.code
            candidate.invalidated_at = now
        if promo.code == IDENTITY_MISMATCH:
            ch.state = RubikaLoginChallengeState.MANUAL_REVIEW_REQUIRED
            ch.failure_code = IDENTITY_MISMATCH
        else:
            ch.state = RubikaLoginChallengeState.LOGIN_FAILED
            ch.failure_code = SESSION_PROMOTION_FAILED
        db.flush()
        return LoginSubmitResult(
            ok=False,
            code=promo.code if promo.code == IDENTITY_MISMATCH else SESSION_PROMOTION_FAILED,
            challenge_id=ch.id,
            state=ch.state.value,
        )

    ch.state = RubikaLoginChallengeState.READY
    ch.completed_session_id = promo.active_session_id
    ch.failure_code = None
    db.flush()

    # Clear ephemeral secrets
    store.pop(ch.id, None)

    # L17: optional automatic pool enrollment (never corrupts ACTIVE on failure).
    pool_block: str | None = None
    from core_engine.services.rubika_l17_automation import (
        POOL_ENROLLMENT_FAILED,
        ensure_rubika_pool_membership,
    )

    enroll = ensure_rubika_pool_membership(db, int(account_id))
    if enroll.code == POOL_ENROLLMENT_FAILED:
        pool_block = POOL_ENROLLMENT_FAILED
        logger.warning(
            "event=pool_enrollment_failed_after_promote account_id=%s detail=%s "
            "active_session_id=%s",
            account_id,
            enroll.message,
            promo.active_session_id,
        )

    auth_ready = evaluate_auth_ready(db, account_id)
    dispatch_ready, dispatch_block = evaluate_dispatch_ready_readonly(db, account_id)
    if pool_block and not dispatch_block:
        dispatch_block = pool_block
        dispatch_ready = False
    elif pool_block:
        dispatch_ready = False

    return LoginSubmitResult(
        ok=True,
        code=LOGIN_READY,
        challenge_id=ch.id,
        state=ch.state.value,
        login_state=RubikaLoginChallengeState.READY.value,
        auth_ready=auth_ready,
        dispatch_ready=bool(dispatch_ready),
        dispatch_block=dispatch_block,
        active_session_id=promo.active_session_id,
        message="Login READY (auth). Dispatch readiness evaluated separately.",
    )


def parse_login_pilot_account_ids(raw: str | None) -> frozenset[int]:
    from workers.account_pool import parse_account_id_list

    return frozenset(parse_account_id_list(raw or ""))


def account_uses_l3_login(account_id: int, db: Session | None = None) -> bool:
    """True when this account must use request/submit L3 state machine (not legacy).

    L17: delegates to ``rubika_l17_automation.decide_l3_login_routing`` when
    ``RUBIKA_L3_LOGIN_ROUTING=auto_evidence`` and ``db`` is provided.
    """
    from core_engine.services.rubika_l17_automation import account_uses_l3_login as _l17

    return _l17(int(account_id), db)


def assert_legacy_login_allowed(account_id: int | None = None, db: Session | None = None) -> None:
    """Fence legacy start/verify when global V1 or evidence/pilot L3 applies."""
    settings = get_settings()
    if bool(settings.RUBIKA_CANONICAL_SESSION_V1):
        blocked = True
    elif account_id is not None and account_uses_l3_login(int(account_id), db):
        blocked = True
    else:
        blocked = False
    if blocked:
        from core_engine.services.rubika_user_session import RubikaLoginError

        raise RubikaLoginError(
            f"{LEGACY_LOGIN_DISABLED}: use request_rubika_login / submit_rubika_login_code"
        )
