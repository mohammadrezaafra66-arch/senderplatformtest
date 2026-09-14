"""Canonical Rubika account lifecycle transitions (Phase 1).

One write path for:
  - create → requires_login
  - validated OTP/session promotion → active
  - canonical session invalidity → requires_login (history kept)

Does not request OTP, send, or delete session/audit rows.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    AccountStatus,
    ChannelSession,
    PlatformType,
    RubikaSessionStatus,
    SessionType,
)

SESSION_INVALIDATED = "SESSION_INVALIDATED"
LOGIN_REQUIRED = "LOGIN_REQUIRED"

# Operator-facing copy. Codes stay canonical; messages are never raw exceptions.
OTP_OPERATOR_MESSAGES_FA: dict[str, str] = {
    "OTP_RATE_LIMITED": "ارسال مجدد کد فعلاً ممکن نیست. لطفاً تا پایان زمان انتظار صبر کنید.",
    "OTP_ALREADY_PENDING": "کد قبلاً ارسال شده و هنوز معتبر است. تا پایان زمان انتظار صبر کنید.",
    "OTP_INVALID": "کد واردشده نادرست است.",
    "OTP_EXPIRED": "کد منقضی شده است. یک کد جدید درخواست کنید.",
    "LOGIN_FAILED": "ورود ناموفق بود. دوباره تلاش کنید.",
    "LOGIN_REQUIRED": "نیاز به ورود",
    "OTP_WAITING": "در انتظار کد",
    "OTP_REQUEST_FAILED": "درخواست کد ناموفق بود. کمی بعد دوباره تلاش کنید.",
    SESSION_INVALIDATED: "نیاز به ورود مجدد",
}

RELOGIN_LABEL_FA = "نیاز به ورود مجدد"


def operator_message(code: str | None, *, fallback: str | None = None) -> str:
    if code and code in OTP_OPERATOR_MESSAGES_FA:
        return OTP_OPERATOR_MESSAGES_FA[code]
    if fallback and not _looks_like_exception(fallback):
        return fallback
    return "عملیات ورود انجام نشد. دوباره تلاش کنید."


def _looks_like_exception(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in ("traceback", "exception", "error:", "sqlalchemy", "nonetype")
    )


def resolve_create_status(platform: PlatformType, requested: AccountStatus) -> AccountStatus:
    """Backend authority for POST /accounts.

    Rubika cannot be created already active. Other platforms keep the request.
    """
    if platform == PlatformType.RUBIKA:
        return AccountStatus.REQUIRES_LOGIN
    return requested


def rubika_active_transition_allowed(db: Session, account: Account) -> bool:
    """True only when a canonical ACTIVE session can already be loaded."""
    if account.platform != PlatformType.RUBIKA:
        return True
    try:
        from core_engine.services.rubika_canonical_session import load_canonical_rubika_session

        load_canonical_rubika_session(db, int(account.id), require_identity_binding=True)
        return True
    except Exception:  # noqa: BLE001 — any canonical miss blocks a manual active flip
        return False


def activate_rubika_account_after_validated_session(account: Account) -> None:
    """Set lifecycle active after a validated session is ACTIVE.

    Does not un-ban. Call only after session promotion succeeded.
    """
    if account.platform != PlatformType.RUBIKA:
        return
    if account.status == AccountStatus.BANNED:
        return
    account.status = AccountStatus.ACTIVE


def apply_rubika_session_invalidation(
    db: Session,
    account: Account,
    *,
    reason: str = SESSION_INVALIDATED,
    clock: datetime | None = None,
) -> None:
    """Demote send-readiness after SessionInvalidError / canonical invalidity.

    Keeps session and audit rows. Marks the current ACTIVE session INVALID so
    it cannot be loaded for dispatch. Does not request OTP or send.
    Banned accounts stay banned (stronger stop). Pool rows stay for history but
    are stamped not-dispatchable so stale membership cannot send.
    """
    if account.platform != PlatformType.RUBIKA:
        return
    now = clock or datetime.now(timezone.utc)
    if account.status != AccountStatus.BANNED:
        account.status = AccountStatus.REQUIRES_LOGIN

    rows = (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == int(account.id),
            ChannelSession.session_type == SessionType.RUBIKA_SESSION,
            ChannelSession.session_status == RubikaSessionStatus.ACTIVE,
        )
        .all()
    )
    code = (reason or SESSION_INVALIDATED)[:64]
    for row in rows:
        row.session_status = RubikaSessionStatus.INVALID
        row.invalidated_at = now
        row.validation_error_code = code

    from core_engine.models import RubikaAccountPool

    pool_rows = (
        db.query(RubikaAccountPool)
        .filter(RubikaAccountPool.account_id == int(account.id))
        .all()
    )
    for pool_row in pool_rows:
        pool_row.last_error_at = now
        pool_row.last_error_message = SESSION_INVALIDATED
    db.flush()
