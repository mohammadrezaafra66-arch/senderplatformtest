"""Rubika account activation — manager confirm after successful login.

Canonical OTP/session promotion is unchanged. This module is the only write
path for the post-login trust gate:

  LOGIN_READY → pending activation → manager confirm → READY_TO_SEND + send pool

When ``RUBIKA_MANAGER_ACTIVATION_REQUIRED`` is on, no Rubika account may send
until status is CONFIRMED (READY_TO_SEND). Missing activation row is pending,
not trusted. Campaign and live send-test preflight must call
``assert_send_activation_allowed``. Activation challenge send uses side-channel
context and never bypasses lifecycle/session preflight.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    PlatformType,
    RubikaAccountActivation,
    RubikaAccountActivationState,
    RubikaAccountPool,
)

logger = logging.getLogger("core_engine.services.rubika_account_activation")

ACTIVATION_PENDING = "ACTIVATION_PENDING"
READY_TO_SEND = "READY_TO_SEND"
NOT_REQUIRED = "NOT_REQUIRED"
RUBIKA_ACTIVATION_PENDING = "rubika_activation_pending"
ACTIVATION_NOT_FOUND = "ACTIVATION_NOT_FOUND"
ACTIVATION_ALREADY_CONFIRMED = "ACTIVATION_ALREADY_CONFIRMED"
ACTIVATION_TOKEN_INVALID = "ACTIVATION_TOKEN_INVALID"
WRONG_PLATFORM = "WRONG_PLATFORM"
MANAGER_PHONE_NOT_CONFIGURED = "MANAGER_PHONE_NOT_CONFIGURED"
POOL_STAMP = "ACTIVATION_PENDING"

_SIDE_CONTEXTS = frozenset({"side_channel", "status", "listener", "ai", "activation"})
_SEND_READY_STATES = frozenset(
    {
        RubikaAccountActivationState.CONFIRMED.value,
        RubikaAccountActivationState.READY_TO_SEND.value,
    }
)
_OPEN_STATES = frozenset(
    {
        RubikaAccountActivationState.PENDING.value,
        RubikaAccountActivationState.CHALLENGE_SENT.value,
        RubikaAccountActivationState.FAILED.value,
    }
)


class ActivationChallengeSender(Protocol):
    async def send(self, *, account_id: int, manager_phone: str, text: str) -> str | None:
        """Send the manager challenge. Returns platform message id if known."""


@dataclass(frozen=True)
class ActivationStartResult:
    required: bool
    pool_enroll_now: bool
    status: str
    send_activation_state: str
    activation_id: int | None = None
    confirm_code: str | None = None


@dataclass(frozen=True)
class ActivationConfirmResult:
    ok: bool
    code: str
    send_activation_state: str
    activation_id: int | None = None
    message: str = ""


def _now(clock: datetime | None = None) -> datetime:
    return clock or datetime.now(timezone.utc)


def _row_state(row: RubikaAccountActivation) -> str:
    raw = row.state
    return str(raw.value if hasattr(raw, "value") else raw)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) < 4:
        return "****"
    return f"****{digits[-4:]}"


def activation_required() -> bool:
    from core_engine.config import get_settings

    return bool(getattr(get_settings(), "RUBIKA_MANAGER_ACTIVATION_REQUIRED", True))


def manager_phone() -> str:
    from core_engine.config import get_settings

    return str(getattr(get_settings(), "RUBIKA_ACTIVATION_MANAGER_PHONE", "") or "").strip()


def latest_activation(db: Session, account_id: int) -> RubikaAccountActivation | None:
    return (
        db.query(RubikaAccountActivation)
        .filter(RubikaAccountActivation.account_id == int(account_id))
        .order_by(RubikaAccountActivation.id.desc())
        .first()
    )


def send_activation_state(db: Session, account_id: int) -> str:
    """READY_TO_SEND | ACTIVATION_PENDING | NOT_REQUIRED.

    Flag on + no CONFIRMED row (including missing row) → ACTIVATION_PENDING.
    """
    if not activation_required():
        return NOT_REQUIRED
    row = latest_activation(db, account_id)
    if row is not None and _row_state(row) in _SEND_READY_STATES:
        return READY_TO_SEND
    return ACTIVATION_PENDING


def campaign_send_requires_activation(context: str | None) -> bool:
    return (context or "worker") not in _SIDE_CONTEXTS


def assert_send_activation_allowed(db: Session, account_id: int, *, context: str = "worker") -> str | None:
    """Return ``rubika_activation_pending`` or None if campaign/live send may proceed.

    Does not bypass session/lifecycle preflight. Callers still run full preflight.
    Side-channel/activation challenge is not a campaign send.
    """
    if not campaign_send_requires_activation(context):
        return None
    state = send_activation_state(db, account_id)
    if state in (READY_TO_SEND, NOT_REQUIRED):
        return None
    return RUBIKA_ACTIVATION_PENDING


def _stamp_pool_pending(db: Session, account_id: int, clock: datetime) -> None:
    rows = (
        db.query(RubikaAccountPool)
        .filter(RubikaAccountPool.account_id == int(account_id))
        .all()
    )
    for row in rows:
        row.last_error_at = clock.replace(tzinfo=None) if clock.tzinfo else clock
        row.last_error_message = POOL_STAMP
    if rows:
        db.flush()


def _clear_pool_pending_stamp(db: Session, account_id: int) -> None:
    rows = (
        db.query(RubikaAccountPool)
        .filter(RubikaAccountPool.account_id == int(account_id))
        .all()
    )
    for row in rows:
        if str(row.last_error_message or "") == POOL_STAMP:
            row.last_error_message = None
            row.last_error_at = None
    if rows:
        db.flush()


def start_activation_after_login(
    db: Session,
    account_id: int,
    *,
    challenge_id: str | None = None,
    session_id: int | None = None,
    clock: datetime | None = None,
) -> ActivationStartResult:
    """Create pending activation after canonical LOGIN_READY. Does not send.

    When the gate is on: do not enroll the send pool here.
    When the gate is off: caller should enroll as before.
    """
    now = _now(clock)
    account = db.query(Account).filter(Account.id == int(account_id)).first()
    if account is None or account.platform != PlatformType.RUBIKA:
        return ActivationStartResult(
            required=False,
            pool_enroll_now=False,
            status="skipped",
            send_activation_state=NOT_REQUIRED,
        )

    if not activation_required():
        return ActivationStartResult(
            required=False,
            pool_enroll_now=True,
            status=NOT_REQUIRED,
            send_activation_state=NOT_REQUIRED,
        )

    raw_token = secrets.token_urlsafe(24)
    confirm_code = secrets.token_hex(4).upper()
    row = latest_activation(db, int(account_id))
    if row is None:
        row = RubikaAccountActivation(account_id=int(account_id), created_at=now)
        db.add(row)
    row.state = RubikaAccountActivationState.PENDING
    row.token_hash = _hash_token(raw_token)
    row.confirm_code = confirm_code
    row.manager_phone = manager_phone() or None
    row.login_challenge_id = challenge_id
    row.session_id = session_id
    row.challenge_message_id = None
    row.confirmed_at = None
    row.confirmed_by = None
    row.last_error = None
    row.updated_at = now
    db.flush()
    _stamp_pool_pending(db, int(account_id), now)
    # Stash one-time token on the instance for the challenge sender in this process.
    row._plaintext_token = raw_token  # type: ignore[attr-defined]
    logger.info(
        "event=rubika_activation_pending account_id=%s activation_id=%s",
        account_id,
        row.id,
    )
    return ActivationStartResult(
        required=True,
        pool_enroll_now=False,
        status=RubikaAccountActivationState.PENDING.value,
        send_activation_state=ACTIVATION_PENDING,
        activation_id=int(row.id),
        confirm_code=confirm_code,
    )


async def run_activation_after_validated_session(
    db: Session,
    account_id: int,
    *,
    challenge_id: str | None = None,
    session_id: int | None = None,
    clock: datetime | None = None,
    sender: ActivationChallengeSender | None = None,
) -> ActivationStartResult:
    """Post-promote hook only. Does not change OTP / prover / promotion."""
    result = start_activation_after_login(
        db,
        account_id,
        challenge_id=challenge_id,
        session_id=session_id,
        clock=clock,
    )
    if result.required:
        await dispatch_activation_challenge(db, int(account_id), sender=sender)
    return result


def _activation_message(*, account: Account, confirm_code: str | None = None) -> str:
    phone = (account.phone_number or "").strip() or f"#{account.id}"
    return (
        "اکانت روبیکا آماده تایید است.\n"
        "شماره:\n"
        f"{phone}\n"
        "\n"
        "برای فعال‌سازی ارسال تایید کنید."
    )


async def dispatch_activation_challenge(
    db: Session,
    account_id: int,
    *,
    sender: ActivationChallengeSender | None = None,
) -> None:
    """Best-effort manager ping. Login remains valid even if this fails."""
    row = latest_activation(db, account_id)
    if row is None or _row_state(row) not in {
        RubikaAccountActivationState.PENDING.value,
        RubikaAccountActivationState.FAILED.value,
    }:
        return
    phone = (row.manager_phone or manager_phone()).strip()
    if not phone:
        row.last_error = MANAGER_PHONE_NOT_CONFIGURED
        row.state = RubikaAccountActivationState.FAILED
        db.flush()
        logger.warning(
            "event=rubika_activation_no_manager_phone account_id=%s activation_id=%s",
            account_id,
            row.id,
        )
        return
    account = db.query(Account).filter(Account.id == int(account_id)).first()
    if account is None:
        return
    text = _activation_message(account=account, confirm_code=row.confirm_code)
    try:
        impl = sender if sender is not None else RubikaUserActivationSender()
        message_id = await impl.send(account_id=int(account_id), manager_phone=phone, text=text)
        row.challenge_message_id = (message_id or None)
        row.state = RubikaAccountActivationState.CHALLENGE_SENT
        row.last_error = None
        row.updated_at = _now()
        db.flush()
    except Exception as exc:  # noqa: BLE001 — login must not fail
        row.last_error = type(exc).__name__[:256]
        row.state = RubikaAccountActivationState.FAILED
        row.updated_at = _now()
        db.flush()
        logger.warning(
            "event=rubika_activation_challenge_failed account_id=%s err=%s",
            account_id,
            type(exc).__name__,
        )


def confirm_activation(
    db: Session,
    account_id: int,
    *,
    actor: str,
    token: str | None = None,
    confirm_code: str | None = None,
    clock: datetime | None = None,
) -> ActivationConfirmResult:
    """Mark READY_TO_SEND and enroll send pool. Operator UI may omit token."""
    now = _now(clock)
    account = db.query(Account).filter(Account.id == int(account_id)).first()
    if account is None or account.platform != PlatformType.RUBIKA:
        return ActivationConfirmResult(
            ok=False,
            code=WRONG_PLATFORM,
            send_activation_state=NOT_REQUIRED,
            message="not_rubika",
        )
    row = latest_activation(db, int(account_id))
    if row is None:
        return ActivationConfirmResult(
            ok=False,
            code=ACTIVATION_NOT_FOUND,
            send_activation_state=send_activation_state(db, int(account_id)),
            message="no_activation_row",
        )
    current = _row_state(row)
    if current in _SEND_READY_STATES:
        return ActivationConfirmResult(
            ok=True,
            code=ACTIVATION_ALREADY_CONFIRMED,
            send_activation_state=READY_TO_SEND,
            activation_id=int(row.id),
            message="already_confirmed",
        )
    if current not in _OPEN_STATES:
        return ActivationConfirmResult(
            ok=False,
            code=ACTIVATION_NOT_FOUND,
            send_activation_state=ACTIVATION_PENDING,
            activation_id=int(row.id),
            message="not_pending",
        )
    if token:
        if _hash_token(token.strip()) != row.token_hash:
            return ActivationConfirmResult(
                ok=False,
                code=ACTIVATION_TOKEN_INVALID,
                send_activation_state=ACTIVATION_PENDING,
                activation_id=int(row.id),
            )
    elif confirm_code:
        if confirm_code.strip().upper() != str(row.confirm_code or "").upper():
            return ActivationConfirmResult(
                ok=False,
                code=ACTIVATION_TOKEN_INVALID,
                send_activation_state=ACTIVATION_PENDING,
                activation_id=int(row.id),
            )
    elif not (actor or "").strip():
        return ActivationConfirmResult(
            ok=False,
            code=ACTIVATION_TOKEN_INVALID,
            send_activation_state=ACTIVATION_PENDING,
            activation_id=int(row.id),
            message="actor_or_token_required",
        )

    row.state = RubikaAccountActivationState.CONFIRMED
    row.confirmed_at = now
    row.confirmed_by = (actor or "manager")[:128]
    row.last_error = None
    row.updated_at = now
    db.flush()
    _clear_pool_pending_stamp(db, int(account_id))

    from core_engine.services.rubika_l17_automation import ensure_rubika_pool_membership

    enroll = ensure_rubika_pool_membership(db, int(account_id))
    logger.info(
        "event=rubika_activation_confirmed account_id=%s activation_id=%s enroll=%s actor=%s",
        account_id,
        row.id,
        enroll.code,
        actor,
    )
    return ActivationConfirmResult(
        ok=True,
        code=READY_TO_SEND,
        send_activation_state=READY_TO_SEND,
        activation_id=int(row.id),
        message="confirmed",
    )


def try_confirm_from_inbound_text(
    db: Session,
    *,
    account_id: int,
    text: str,
    actor: str = "rubika_inbound",
) -> ActivationConfirmResult | None:
    """Match manager reply containing the confirm code. No webhook exists today."""
    needle = (text or "").strip().upper()
    if not needle:
        return None
    row = latest_activation(db, int(account_id))
    if row is None or _row_state(row) not in _OPEN_STATES:
        return None
    code = str(row.confirm_code or "").upper()
    if code and code in needle:
        return confirm_activation(db, int(account_id), actor=actor, confirm_code=code)
    return None


class RubikaUserActivationSender:
    """Live manager ping via the newly logged-in user session (not campaign worker)."""

    async def send(self, *, account_id: int, manager_phone: str, text: str) -> str | None:
        from core_engine.database import SessionLocal
        from core_engine.services.rubika_preflight import require_rubika_side_channel_send
        from workers.connectors.rubika_user import (
            _connect_authenticated,
            _deep_find,
            load_rubika_user_client,
        )

        db = SessionLocal()
        client = None
        try:
            gate = await require_rubika_side_channel_send(
                db, account_id=int(account_id), context="activation"
            )
            if not gate.allowed:
                raise RuntimeError(gate.code)
            client = await load_rubika_user_client(int(account_id), db)
            # Same live-send prep: connect + decode_auth + import_key.
            await _connect_authenticated(client)
            phone = (manager_phone or "").strip()
            if phone.startswith("0"):
                phone = f"98{phone[1:]}"
            book = await client.add_address_book(
                phone=phone, first_name="Manager", last_name=""
            )
            guid = str(_deep_find(book, "user_guid") or "").strip()
            if not guid:
                raise RuntimeError("manager_guid_unresolved")
            logger.info(
                "event=rubika_activation_send_begin account_id=%s manager_phone=%s guid=%s",
                account_id,
                _mask_phone(phone),
                guid,
            )
            result = await client.send_message(object_guid=guid, text=text)
            message_id = str(_deep_find(result, "message_id") or "").strip() or None
            logger.info(
                "event=rubika_activation_send_result account_id=%s message_id=%s",
                account_id,
                message_id,
            )
            return message_id
        finally:
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            db.close()
