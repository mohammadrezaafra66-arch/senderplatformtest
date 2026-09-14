"""L18 — Authoritative account runtime status for API/UI truth.

Frontend must render these fields; do not derive "connected" from Account.status.
Never exposes session/token secrets.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    AccountStatus,
    ChannelSession,
    PlatformType,
    RubikaLoginChallenge,
    RubikaLoginChallengeState,
    RubikaSessionStatus,
    SessionType,
)
from core_engine.services.account_session_wiring import (
    SESSION_DECRYPT_FAILED,
    SESSION_INVALID,
    SESSION_MISSING,
    evaluate_account_session_readiness,
)
from core_engine.services.crypto import SessionDecryptionError

logger = logging.getLogger("core_engine.account_runtime_status")


class RuntimeStatus(str, Enum):
    DISABLED = "DISABLED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    OTP_WAITING = "OTP_WAITING"
    AUTHENTICATING = "AUTHENTICATING"
    AUTHENTICATED_NO_WORKER = "AUTHENTICATED_NO_WORKER"
    READY = "READY"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    SESSION_ERROR = "SESSION_ERROR"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    CONFIG_ERROR = "CONFIG_ERROR"
    NOT_APPLICABLE = "NOT_APPLICABLE"


# Persian operational labels (authoritative for UI; frontend may mirror).
AUTHENTICATED_LABEL_FA = "احراز شده"
WORKER_READY_LABEL_FA = "Worker آماده"
WORKER_STALE_LABEL_FA = "Worker قطع شده"
QUARANTINE_LABEL_FA = "قرنطینه"

RUNTIME_STATUS_LABEL_FA: dict[str, str] = {
    RuntimeStatus.DISABLED.value: "غیرفعال",
    RuntimeStatus.LOGIN_REQUIRED.value: "نیاز به ورود",
    RuntimeStatus.OTP_WAITING.value: "در انتظار کد",
    RuntimeStatus.AUTHENTICATING.value: "در حال احراز",
    RuntimeStatus.AUTHENTICATED_NO_WORKER.value: "احراز شده، Worker آماده نیست",
    RuntimeStatus.READY.value: "آماده ارسال",
    RuntimeStatus.MANUAL_REVIEW.value: "نیازمند بررسی",
    RuntimeStatus.SESSION_ERROR.value: "خطای سشن",
    RuntimeStatus.CONNECTION_ERROR.value: "خطای اتصال",
    RuntimeStatus.CONFIG_ERROR.value: "خطای پیکربندی",
    RuntimeStatus.NOT_APPLICABLE.value: "نامرتبط",
}

OPERATOR_ACTION_LABEL_FA: dict[str, str] = {
    "NONE": "اقدامی لازم نیست",
    "ENABLE_ACCOUNT": "فعال‌سازی اکانت",
    "LOGIN": "ورود / درخواست کد",
    "SUBMIT_OTP": "ورود کد یک‌بارمصرف",
    "WAIT_AUTH": "منتظر تکمیل احراز هویت",
    "MANUAL_REVIEW": "بررسی دستی توسط اپراتور",
    "RELOGIN": "ورود مجدد",
    "FIX_CONNECTION": "بررسی اتصال / تست اتصال",
    "FIX_CONFIG": "بررسی پیکربندی",
    "WAIT_WORKER": "منتظر پوشش worker",
}

_OTP_WAITING_STATES = frozenset(
    {
        RubikaLoginChallengeState.OTP_REQUESTED,
        RubikaLoginChallengeState.OTP_WAITING_FOR_OPERATOR,
        RubikaLoginChallengeState.OTP_SUBMITTED,
    }
)
_AUTHENTICATING_STATES = frozenset(
    {
        RubikaLoginChallengeState.AUTHENTICATING,
        RubikaLoginChallengeState.IDENTITY_VERIFYING,
        RubikaLoginChallengeState.SESSION_PERSISTING,
    }
)


@dataclass(slots=True)
class AccountRuntimeStatus:
    account_id: int
    platform: str
    enabled: bool
    runtime_status: str
    runtime_status_label: str
    auth_state: str
    auth_reason: str | None
    credential_type: str | None
    credential_state: str
    identity_state: str
    worker_state: str
    worker_covered: bool | None
    worker_heartbeat_fresh: bool | None
    dispatch_ready: bool
    dispatch_blocker: str | None
    operator_action_code: str
    operator_action_label: str
    reason_code: str
    last_verified_at: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "platform": self.platform,
            "enabled": self.enabled,
            "runtime_status": self.runtime_status,
            "runtime_status_label": self.runtime_status_label,
            "auth": {"state": self.auth_state, "reason": self.auth_reason},
            "credential": {"type": self.credential_type, "state": self.credential_state},
            "identity": {"state": self.identity_state},
            "worker": {
                "state": self.worker_state,
                "covered": self.worker_covered,
                "heartbeat_fresh": self.worker_heartbeat_fresh,
            },
            "dispatch": {"ready": self.dispatch_ready, "blocker": self.dispatch_blocker},
            "operator_action": {
                "code": self.operator_action_code,
                "label": self.operator_action_label,
            },
            "reason_code": self.reason_code,
            "last_verified_at": self.last_verified_at,
        }

    def to_flat_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("details", None)
        return d


def runtime_status_label(status: str) -> str:
    return RUNTIME_STATUS_LABEL_FA.get(status, status)


def operator_action_label(code: str) -> str:
    return OPERATOR_ACTION_LABEL_FA.get(code, code)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def coverage_liveness(*, fresh: bool, last_seen: bool) -> str:
    """fresh = coverage key exists. stale = last-seen only. missing = never covered."""
    if fresh:
        return "fresh"
    if last_seen:
        return "stale"
    return "missing"


def batch_worker_coverage_state(
    *,
    platform: str,
    account_ids: list[int],
) -> dict[int, str]:
    """Per-account coverage liveness. Fail soft to missing (not a fake fresh worker)."""
    if not account_ids:
        return {}
    try:
        import redis

        from core_engine.config import get_settings
        from workers.redis_keys import (
            worker_account_coverage_key,
            worker_account_coverage_last_key,
        )

        client = redis.from_url(get_settings().REDIS_URL, decode_responses=True)
        try:
            pipe = client.pipeline()
            for aid in account_ids:
                pipe.exists(worker_account_coverage_key(platform, int(aid)))
                pipe.exists(worker_account_coverage_last_key(platform, int(aid)))
            results = pipe.execute()
            out: dict[int, str] = {}
            for index, aid in enumerate(account_ids):
                fresh = bool(results[index * 2])
                last_seen = bool(results[index * 2 + 1])
                out[int(aid)] = coverage_liveness(fresh=fresh, last_seen=last_seen)
            return out
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("event=worker_coverage_state_failed platform=%s err=%s", platform, exc)
        return {int(aid): "missing" for aid in account_ids}


def rubika_account_quarantined(account_id: int) -> bool:
    """True only when Redis confirms quarantine. Fail open (not a fake READY)."""
    try:
        import redis

        from core_engine.config import get_settings
        from workers.redis_keys import rubika_quarantine_key

        client = redis.from_url(
            get_settings().REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        )
        try:
            return bool(client.exists(rubika_quarantine_key(int(account_id))))
        finally:
            client.close()
    except Exception:  # noqa: BLE001
        return False


def batch_worker_coverage(
    *,
    platform: str,
    account_ids: list[int],
) -> dict[int, bool]:
    """Sync Redis EXISTS for coverage keys. Empty dict on Redis failure (fail soft)."""
    if not account_ids:
        return {}
    try:
        import redis

        from core_engine.config import get_settings
        from workers.redis_keys import worker_account_coverage_key

        client = redis.from_url(get_settings().REDIS_URL, decode_responses=True)
        try:
            pipe = client.pipeline()
            for aid in account_ids:
                pipe.exists(worker_account_coverage_key(platform, int(aid)))
            results = pipe.execute()
            return {int(aid): bool(val) for aid, val in zip(account_ids, results)}
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("event=worker_coverage_batch_failed platform=%s err=%s", platform, exc)
        return {int(aid): False for aid in account_ids}


def _latest_open_challenge(db: Session, account_id: int) -> RubikaLoginChallenge | None:
    return (
        db.query(RubikaLoginChallenge)
        .filter(RubikaLoginChallenge.account_id == int(account_id))
        .order_by(RubikaLoginChallenge.created_at.desc())
        .first()
    )


def _active_otp_or_auth_challenge(
    db: Session, account_id: int, *, now: datetime | None = None
) -> tuple[RubikaLoginChallenge | None, str | None]:
    """Return (challenge, kind) where kind in otp|auth|manual|None.

    Expired OTP challenges do NOT stay OTP_WAITING — caller treats as login required.
    """
    now = now or datetime.now(timezone.utc)
    ch = _latest_open_challenge(db, account_id)
    if ch is None:
        return None, None
    state = ch.state
    if state == RubikaLoginChallengeState.MANUAL_REVIEW_REQUIRED:
        return ch, "manual"
    if state in _OTP_WAITING_STATES:
        exp = _aware(ch.expires_at)
        if exp is not None and exp <= now:
            return ch, "expired_otp"
        return ch, "otp"
    if state in _AUTHENTICATING_STATES:
        return ch, "auth"
    return None, None


def _rubika_session_rows(db: Session, account_id: int) -> list[ChannelSession]:
    return (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == int(account_id),
            ChannelSession.session_type == SessionType.RUBIKA_SESSION,
        )
        .order_by(ChannelSession.id.asc())
        .all()
    )


def _try_decrypt_session(row: ChannelSession) -> tuple[bool, str | None]:
    try:
        from core_engine.services.session_storage import load_channel_session_plaintext

        plaintext = load_channel_session_plaintext(row)
        if not plaintext:
            return False, SESSION_DECRYPT_FAILED
        return True, None
    except SessionDecryptionError:
        return False, SESSION_DECRYPT_FAILED
    except Exception:  # noqa: BLE001
        return False, SESSION_INVALID


def _make(
    account: Account,
    *,
    status: RuntimeStatus,
    reason_code: str,
    auth_state: str,
    auth_reason: str | None = None,
    credential_type: str | None = None,
    credential_state: str = "unknown",
    identity_state: str = "unknown",
    worker_state: str = "unknown",
    worker_covered: bool | None = None,
    worker_heartbeat_fresh: bool | None = None,
    dispatch_ready: bool = False,
    dispatch_blocker: str | None = None,
    operator_action_code: str = "NONE",
    details: dict[str, Any] | None = None,
    runtime_label: str | None = None,
) -> AccountRuntimeStatus:
    enabled = account.status == AccountStatus.ACTIVE
    return AccountRuntimeStatus(
        account_id=int(account.id),
        platform=account.platform.value if hasattr(account.platform, "value") else str(account.platform),
        enabled=enabled,
        runtime_status=status.value,
        runtime_status_label=runtime_label or runtime_status_label(status.value),
        auth_state=auth_state,
        auth_reason=auth_reason,
        credential_type=credential_type,
        credential_state=credential_state,
        identity_state=identity_state,
        worker_state=worker_state,
        worker_covered=worker_covered,
        worker_heartbeat_fresh=worker_heartbeat_fresh,
        dispatch_ready=dispatch_ready,
        dispatch_blocker=dispatch_blocker,
        operator_action_code=operator_action_code,
        operator_action_label=operator_action_label(operator_action_code),
        reason_code=reason_code,
        last_verified_at=_now_iso(),
        details=details or {},
    )


def _quarantine_block(account: Account) -> AccountRuntimeStatus:
    return _make(
        account,
        status=RuntimeStatus.AUTHENTICATED_NO_WORKER,
        reason_code="QUARANTINED",
        auth_state="authenticated",
        credential_type="rubika_session",
        credential_state="active",
        identity_state="bound",
        worker_state="excluded",
        worker_covered=False,
        worker_heartbeat_fresh=False,
        dispatch_ready=False,
        dispatch_blocker="QUARANTINED",
        operator_action_code="MANUAL_REVIEW",
        runtime_label=QUARANTINE_LABEL_FA,
    )


def _worker_gap(
    *,
    covered: bool,
    stale: bool,
    eligible: bool,
    legacy: bool,
) -> dict[str, Any]:
    """Authenticated, but not READY. Coverage never crosses accounts."""
    credential_state = "legacy_usable" if legacy else "active"
    identity_state = "legacy" if legacy else "bound"
    common = {
        "status": RuntimeStatus.AUTHENTICATED_NO_WORKER,
        "auth_state": "authenticated",
        "credential_type": "rubika_session",
        "credential_state": credential_state,
        "identity_state": identity_state,
        "dispatch_ready": False,
        "operator_action_code": "WAIT_WORKER",
    }
    if covered and not eligible:
        return {
            **common,
            "reason_code": "WORKER_COVERED_NOT_DISPATCH",
            "worker_state": "covered",
            "worker_covered": True,
            "worker_heartbeat_fresh": True,
            "dispatch_blocker": "NOT_DISPATCH_ELIGIBLE",
            "runtime_label": WORKER_READY_LABEL_FA,
        }
    if stale:
        return {
            **common,
            "reason_code": "WORKER_STALE",
            "worker_state": "stale",
            "worker_covered": False,
            "worker_heartbeat_fresh": False,
            "dispatch_blocker": "NO_WORKER_CONSUMER",
            "runtime_label": WORKER_STALE_LABEL_FA,
        }
    return {
        **common,
        "reason_code": "LEGACY_NO_WORKER" if legacy else "NO_WORKER_COVERAGE",
        "worker_state": "missing",
        "worker_covered": False,
        "worker_heartbeat_fresh": False,
        "dispatch_blocker": "NO_WORKER_CONSUMER",
        "runtime_label": None,
    }


def compute_rubika_runtime_status(
    db: Session,
    account: Account,
    *,
    worker_covered: bool | None = None,
    worker_stale: bool = False,
    dispatch_eligible_ids: set[int] | None = None,
) -> AccountRuntimeStatus:
    from core_engine.services.rubika_canonical_session import (
        CanonicalSessionError,
        load_canonical_rubika_session,
    )
    from core_engine.services.rubika_l17_automation import (
        account_is_canonical_managed,
        account_is_legacy_protected,
        count_rubika_sessions,
    )
    from core_engine.services.rubika_mode import (
        RUBIKA_MODE_BOT_API,
        resolve_rubika_delivery_mode,
    )

    mode = resolve_rubika_delivery_mode()
    if mode == RUBIKA_MODE_BOT_API:
        return compute_token_platform_runtime_status(
            db, account, credential_type="api_token", worker_covered=worker_covered
        )

    if account.status == AccountStatus.BANNED:
        return _make(
            account,
            status=RuntimeStatus.DISABLED,
            reason_code="ACCOUNT_BANNED",
            auth_state="disabled",
            credential_type="rubika_session",
            credential_state="n/a",
            identity_state="n/a",
            worker_state="excluded",
            worker_covered=False,
            operator_action_code="ENABLE_ACCOUNT",
            runtime_label="مسدود",
        )
    if account.status == AccountStatus.RESTING:
        return _make(
            account,
            status=RuntimeStatus.DISABLED,
            reason_code="ACCOUNT_RESTING",
            auth_state="disabled",
            credential_type="rubika_session",
            credential_state="n/a",
            identity_state="n/a",
            worker_state="excluded",
            worker_covered=False,
            operator_action_code="ENABLE_ACCOUNT",
            runtime_label="متوقف",
        )

    ch, kind = _active_otp_or_auth_challenge(db, account.id)
    if kind == "manual":
        return _make(
            account,
            status=RuntimeStatus.MANUAL_REVIEW,
            reason_code="LOGIN_MANUAL_REVIEW",
            auth_state="manual_review",
            auth_reason=ch.failure_code if ch else None,
            credential_type="rubika_session",
            credential_state="ambiguous",
            identity_state="unverified",
            worker_state="covered" if worker_covered else "missing",
            worker_covered=bool(worker_covered),
            worker_heartbeat_fresh=bool(worker_covered),
            operator_action_code="MANUAL_REVIEW",
            details={"challenge_id": ch.id if ch else None},
        )
    if kind == "otp":
        return _make(
            account,
            status=RuntimeStatus.OTP_WAITING,
            reason_code="OTP_WAITING",
            auth_state="otp_waiting",
            credential_type="rubika_session",
            credential_state="pending_login",
            identity_state="unverified",
            worker_state="covered" if worker_covered else "missing",
            worker_covered=bool(worker_covered),
            worker_heartbeat_fresh=bool(worker_covered),
            operator_action_code="SUBMIT_OTP",
            details={"challenge_id": ch.id if ch else None},
        )
    if kind == "auth":
        return _make(
            account,
            status=RuntimeStatus.AUTHENTICATING,
            reason_code="AUTHENTICATING",
            auth_state="authenticating",
            credential_type="rubika_session",
            credential_state="pending_login",
            identity_state="verifying",
            worker_state="covered" if worker_covered else "missing",
            worker_covered=bool(worker_covered),
            worker_heartbeat_fresh=bool(worker_covered),
            operator_action_code="WAIT_AUTH",
            details={"challenge_id": ch.id if ch else None},
        )

    if account.status == AccountStatus.REQUIRES_LOGIN:
        rows = _rubika_session_rows(db, account.id)
        had_invalid = any(r.session_status == RubikaSessionStatus.INVALID for r in rows)
        return _make(
            account,
            status=RuntimeStatus.LOGIN_REQUIRED,
            reason_code="SESSION_INVALIDATED" if had_invalid else "ACCOUNT_REQUIRES_LOGIN",
            auth_state="unauthenticated",
            auth_reason="session_invalid" if had_invalid else None,
            credential_type="rubika_session",
            credential_state="invalid" if had_invalid else "requires_login",
            identity_state="unbound",
            worker_state="covered" if worker_covered else "missing",
            worker_covered=bool(worker_covered),
            worker_heartbeat_fresh=bool(worker_covered),
            dispatch_ready=False,
            dispatch_blocker="LOGIN_REQUIRED",
            operator_action_code="RELOGIN" if had_invalid else "LOGIN",
            details={"expired_otp": kind == "expired_otp", "session_invalidated": had_invalid},
            runtime_label="نیاز به ورود مجدد" if had_invalid else None,
        )

    rows = _rubika_session_rows(db, account.id)
    actives = [r for r in rows if r.session_status == RubikaSessionStatus.ACTIVE]
    terminal = {
        RubikaSessionStatus.INVALID,
        RubikaSessionStatus.DECRYPT_FAILED,
        RubikaSessionStatus.REVOKED,
    }
    if rows and not actives and all(r.session_status in terminal for r in rows):
        return _make(
            account,
            status=RuntimeStatus.LOGIN_REQUIRED,
            reason_code="SESSION_INVALIDATED",
            auth_state="unauthenticated",
            auth_reason="session_invalid",
            credential_type="rubika_session",
            credential_state="invalid",
            identity_state="unbound",
            worker_state="excluded",
            worker_covered=False,
            worker_heartbeat_fresh=False,
            dispatch_ready=False,
            dispatch_blocker="LOGIN_REQUIRED",
            operator_action_code="RELOGIN",
            runtime_label="نیاز به ورود مجدد",
        )
    session_count = len(rows)
    legacy_protected = account_is_legacy_protected(db, account.id)
    canonical_managed = account_is_canonical_managed(db, account.id)

    # Ambiguous multi-session inventory (e.g. Account12) — never READY merely for pin.
    if session_count > 1 and not canonical_managed:
        return _make(
            account,
            status=RuntimeStatus.MANUAL_REVIEW,
            reason_code="LEGACY_MULTI_SESSION",
            auth_state="ambiguous",
            auth_reason=f"sessions={session_count};actives={len(actives)}",
            credential_type="rubika_session",
            credential_state="ambiguous",
            identity_state="unverified",
            worker_state="covered" if worker_covered else "missing",
            worker_covered=bool(worker_covered),
            worker_heartbeat_fresh=bool(worker_covered),
            dispatch_ready=False,
            dispatch_blocker="MANUAL_REVIEW",
            operator_action_code="MANUAL_REVIEW",
            details={
                "session_ids": [int(r.id) for r in rows],
                "active_ids": [int(r.id) for r in actives],
                "legacy_protected": legacy_protected,
            },
        )

    if session_count == 0 or (account.status == AccountStatus.REQUIRES_LOGIN and not actives):
        # Expired OTP already handled above → LOGIN_REQUIRED (not permanently waiting).
        return _make(
            account,
            status=RuntimeStatus.LOGIN_REQUIRED,
            reason_code="NO_USABLE_SESSION" if session_count == 0 else "ACCOUNT_REQUIRES_LOGIN",
            auth_state="unauthenticated",
            credential_type="rubika_session",
            credential_state="missing" if session_count == 0 else "requires_login",
            identity_state="unbound",
            worker_state="covered" if worker_covered else "missing",
            worker_covered=bool(worker_covered),
            worker_heartbeat_fresh=bool(worker_covered),
            operator_action_code="LOGIN",
            details={"expired_otp": kind == "expired_otp"},
        )

    # Canonical ACTIVE path
    if canonical_managed or len(actives) == 1:
        try:
            load_canonical_rubika_session(db, account.id, require_identity_binding=True)
            auth_ok = True
            identity_state = "bound"
            cred_state = "active"
            auth_state = "authenticated"
            dec_err = None
        except CanonicalSessionError as exc:
            auth_ok = False
            identity_state = "mismatch_or_unbound"
            cred_state = "invalid"
            auth_state = "error"
            dec_err = str(getattr(exc, "code", None) or type(exc).__name__)
            # Distinguish decrypt vs connection-ish failures when single active exists.
            if actives:
                ok_dec, code = _try_decrypt_session(actives[0])
                if not ok_dec:
                    return _make(
                        account,
                        status=RuntimeStatus.SESSION_ERROR,
                        reason_code=code or "SESSION_ERROR",
                        auth_state="error",
                        auth_reason=code,
                        credential_type="rubika_session",
                        credential_state="decrypt_failed",
                        identity_state=identity_state,
                        worker_state="covered" if worker_covered else "missing",
                        worker_covered=bool(worker_covered),
                        worker_heartbeat_fresh=bool(worker_covered),
                        operator_action_code="RELOGIN",
                    )
            return _make(
                account,
                status=RuntimeStatus.CONNECTION_ERROR
                if actives
                else RuntimeStatus.LOGIN_REQUIRED,
                reason_code=dec_err or "CANONICAL_AUTH_FAILED",
                auth_state=auth_state,
                auth_reason=dec_err,
                credential_type="rubika_session",
                credential_state=cred_state,
                identity_state=identity_state,
                worker_state="covered" if worker_covered else "missing",
                worker_covered=bool(worker_covered),
                worker_heartbeat_fresh=bool(worker_covered),
                operator_action_code="FIX_CONNECTION" if actives else "LOGIN",
            )

        if auth_ok:
            eligible = True
            if dispatch_eligible_ids is not None:
                eligible = int(account.id) in dispatch_eligible_ids
            covered = bool(worker_covered)
            if covered and eligible and account.status == AccountStatus.ACTIVE:
                if rubika_account_quarantined(int(account.id)):
                    return _quarantine_block(account)
                return _make(
                    account,
                    status=RuntimeStatus.READY,
                    reason_code="READY",
                    auth_state="authenticated",
                    credential_type="rubika_session",
                    credential_state="active",
                    identity_state="bound",
                    worker_state="covered",
                    worker_covered=True,
                    worker_heartbeat_fresh=True,
                    dispatch_ready=True,
                    operator_action_code="NONE",
                    details={"canonical_managed": canonical_managed, "auth_label": AUTHENTICATED_LABEL_FA},
                )
            gap = _worker_gap(
                covered=covered,
                stale=bool(worker_stale) and not covered,
                eligible=eligible,
                legacy=False,
            )
            return _make(account, details={"auth_label": AUTHENTICATED_LABEL_FA}, **gap)

    # Single legacy session (e.g. Account79) — evidence-based, not invented canonical.
    if session_count == 1:
        row = rows[0]
        ok_dec, code = _try_decrypt_session(row)
        if not ok_dec:
            return _make(
                account,
                status=RuntimeStatus.SESSION_ERROR,
                reason_code=code or "SESSION_DECRYPT_FAILED",
                auth_state="error",
                auth_reason=code,
                credential_type="rubika_session",
                credential_state="decrypt_failed",
                identity_state="legacy_unverified",
                worker_state="covered" if worker_covered else "missing",
                worker_covered=bool(worker_covered),
                worker_heartbeat_fresh=bool(worker_covered),
                operator_action_code="RELOGIN",
                details={"session_id": int(row.id), "legacy": True},
            )
        eligible = (
            dispatch_eligible_ids is None or int(account.id) in dispatch_eligible_ids
        )
        covered = bool(worker_covered)
        if covered and eligible and account.status == AccountStatus.ACTIVE:
            if rubika_account_quarantined(int(account.id)):
                return _quarantine_block(account)
            return _make(
                account,
                status=RuntimeStatus.READY,
                reason_code="LEGACY_READY",
                auth_state="authenticated",
                credential_type="rubika_session",
                credential_state="legacy_usable",
                identity_state="legacy",
                worker_state="covered",
                worker_covered=True,
                worker_heartbeat_fresh=True,
                dispatch_ready=True,
                operator_action_code="NONE",
                details={"session_id": int(row.id), "legacy": True, "canonical": False},
            )
        if covered and not eligible:
            gap = _worker_gap(covered=True, stale=False, eligible=False, legacy=True)
            if legacy_protected:
                return _make(
                    account,
                    status=RuntimeStatus.MANUAL_REVIEW,
                    reason_code="LEGACY_NOT_DISPATCH_ELIGIBLE",
                    auth_state="authenticated",
                    credential_type="rubika_session",
                    credential_state="legacy_usable",
                    identity_state="legacy",
                    worker_state="covered",
                    worker_covered=True,
                    worker_heartbeat_fresh=True,
                    dispatch_ready=False,
                    dispatch_blocker="NOT_DISPATCH_ELIGIBLE",
                    operator_action_code="MANUAL_REVIEW",
                    details={"session_id": int(row.id), "legacy": True},
                )
            return _make(
                account,
                details={"session_id": int(row.id), "legacy": True, "auth_label": AUTHENTICATED_LABEL_FA},
                **gap,
            )
        gap = _worker_gap(
            covered=False,
            stale=bool(worker_stale),
            eligible=eligible,
            legacy=True,
        )
        return _make(
            account,
            details={"session_id": int(row.id), "legacy": True, "auth_label": AUTHENTICATED_LABEL_FA},
            **gap,
        )

    # Remaining ambiguous inventory
    if legacy_protected or session_count > 1:
        return _make(
            account,
            status=RuntimeStatus.MANUAL_REVIEW,
            reason_code="LEGACY_UNCLASSIFIED",
            auth_state="ambiguous",
            credential_type="rubika_session",
            credential_state="ambiguous",
            identity_state="unverified",
            worker_state="covered" if worker_covered else "missing",
            worker_covered=bool(worker_covered),
            worker_heartbeat_fresh=bool(worker_covered),
            operator_action_code="MANUAL_REVIEW",
            details={"session_count": session_count},
        )

    return _make(
        account,
        status=RuntimeStatus.LOGIN_REQUIRED,
        reason_code="NO_USABLE_SESSION",
        auth_state="unauthenticated",
        credential_type="rubika_session",
        credential_state="missing",
        identity_state="unbound",
        worker_state="covered" if worker_covered else "missing",
        worker_covered=bool(worker_covered),
        operator_action_code="LOGIN",
        details={"session_count": count_rubika_sessions(db, account.id)},
    )


def compute_token_platform_runtime_status(
    db: Session,
    account: Account,
    *,
    credential_type: str = "api_token",
    worker_covered: bool | None = None,
) -> AccountRuntimeStatus:
    if account.status == AccountStatus.BANNED:
        return _make(
            account,
            status=RuntimeStatus.DISABLED,
            reason_code="ACCOUNT_BANNED",
            auth_state="disabled",
            credential_type=credential_type,
            credential_state="n/a",
            identity_state="n/a",
            worker_state="not_applicable",
            operator_action_code="ENABLE_ACCOUNT",
        )
    if account.status == AccountStatus.RESTING:
        return _make(
            account,
            status=RuntimeStatus.DISABLED,
            reason_code="ACCOUNT_RESTING",
            auth_state="disabled",
            credential_type=credential_type,
            credential_state="n/a",
            identity_state="n/a",
            worker_state="not_applicable",
            operator_action_code="ENABLE_ACCOUNT",
        )

    readiness = evaluate_account_session_readiness(db, account)
    code = readiness.code or ("READY" if readiness.ready else SESSION_MISSING)

    if account.status == AccountStatus.REQUIRES_LOGIN or code in {
        "ACCOUNT_REQUIRES_LOGIN",
        SESSION_MISSING,
    }:
        return _make(
            account,
            status=RuntimeStatus.LOGIN_REQUIRED,
            reason_code=code,
            auth_state="unauthenticated",
            credential_type=credential_type,
            credential_state="missing",
            identity_state="n/a",
            worker_state="not_applicable" if worker_covered is None else (
                "covered" if worker_covered else "missing"
            ),
            worker_covered=worker_covered,
            operator_action_code="LOGIN",
        )
    if code in {SESSION_DECRYPT_FAILED, SESSION_INVALID}:
        return _make(
            account,
            status=RuntimeStatus.SESSION_ERROR,
            reason_code=code,
            auth_state="error",
            auth_reason=readiness.error,
            credential_type=credential_type,
            credential_state="invalid",
            identity_state="n/a",
            worker_state="not_applicable",
            operator_action_code="RELOGIN",
        )
    if code in {"CONFIG_INVALID", "DELIVERY_MODE_DISABLED", "USER_ACCOUNT_DISABLED"}:
        return _make(
            account,
            status=RuntimeStatus.CONFIG_ERROR,
            reason_code=code,
            auth_state="error",
            auth_reason=readiness.error,
            credential_type=credential_type,
            credential_state="config_error",
            identity_state="n/a",
            worker_state="not_applicable",
            operator_action_code="FIX_CONFIG",
        )
    if readiness.ready:
        # Token bots: credential ready == dispatch-ready at account level (no Rubika worker model).
        return _make(
            account,
            status=RuntimeStatus.READY,
            reason_code="READY",
            auth_state="authenticated",
            credential_type=credential_type,
            credential_state="valid",
            identity_state="n/a",
            worker_state="not_applicable",
            worker_covered=worker_covered,
            dispatch_ready=True,
            operator_action_code="NONE",
        )
    return _make(
        account,
        status=RuntimeStatus.CONNECTION_ERROR,
        reason_code=code,
        auth_state="error",
        auth_reason=readiness.error,
        credential_type=credential_type,
        credential_state="not_ready",
        identity_state="n/a",
        worker_state="not_applicable",
        operator_action_code="FIX_CONNECTION",
    )


def compute_whatsapp_runtime_status(
    db: Session,
    account: Account,
    *,
    worker_covered: bool | None = None,
) -> AccountRuntimeStatus:
    if account.status in {AccountStatus.BANNED, AccountStatus.RESTING}:
        return _make(
            account,
            status=RuntimeStatus.DISABLED,
            reason_code="ACCOUNT_BANNED"
            if account.status == AccountStatus.BANNED
            else "ACCOUNT_RESTING",
            auth_state="disabled",
            credential_type="whatsapp",
            credential_state="n/a",
            identity_state="n/a",
            worker_state="excluded",
            operator_action_code="ENABLE_ACCOUNT",
        )

    readiness = evaluate_account_session_readiness(db, account)
    code = readiness.code or ("READY" if readiness.ready else SESSION_MISSING)
    stype = readiness.session_type or "whatsapp"

    if code in {"WHATSAPP_WEB_NOT_LINKED", "EVOLUTION_NOT_CONNECTED", SESSION_MISSING}:
        return _make(
            account,
            status=RuntimeStatus.LOGIN_REQUIRED,
            reason_code=code,
            auth_state="unauthenticated",
            credential_type=stype,
            credential_state="not_linked",
            identity_state="n/a",
            worker_state="covered" if worker_covered else "missing",
            worker_covered=bool(worker_covered) if worker_covered is not None else None,
            operator_action_code="LOGIN",
        )
    if code in {SESSION_DECRYPT_FAILED, SESSION_INVALID}:
        return _make(
            account,
            status=RuntimeStatus.SESSION_ERROR,
            reason_code=code,
            auth_state="error",
            credential_type=stype,
            credential_state="invalid",
            identity_state="n/a",
            worker_state="covered" if worker_covered else "missing",
            worker_covered=bool(worker_covered) if worker_covered is not None else None,
            operator_action_code="RELOGIN",
        )
    if readiness.ready:
        covered = worker_covered
        if covered is False:
            return _make(
                account,
                status=RuntimeStatus.AUTHENTICATED_NO_WORKER,
                reason_code="NO_WORKER_COVERAGE",
                auth_state="authenticated",
                credential_type=stype,
                credential_state="linked",
                identity_state="n/a",
                worker_state="missing",
                worker_covered=False,
                worker_heartbeat_fresh=False,
                dispatch_ready=False,
                dispatch_blocker="NO_WORKER_CONSUMER",
                operator_action_code="WAIT_WORKER",
            )
        return _make(
            account,
            status=RuntimeStatus.READY,
            reason_code="READY",
            auth_state="authenticated",
            credential_type=stype,
            credential_state="linked",
            identity_state="n/a",
            worker_state="covered" if covered else "not_applicable",
            worker_covered=covered,
            worker_heartbeat_fresh=covered,
            dispatch_ready=True,
            operator_action_code="NONE",
        )
    return _make(
        account,
        status=RuntimeStatus.CONNECTION_ERROR,
        reason_code=code,
        auth_state="error",
        auth_reason=readiness.error,
        credential_type=stype,
        credential_state="not_ready",
        identity_state="n/a",
        worker_state="covered" if worker_covered else "missing",
        worker_covered=bool(worker_covered) if worker_covered is not None else None,
        operator_action_code="FIX_CONNECTION",
    )


def compute_account_runtime_status(
    db: Session,
    account: Account,
    *,
    worker_covered: bool | None = None,
    worker_stale: bool = False,
    dispatch_eligible_ids: set[int] | None = None,
) -> AccountRuntimeStatus:
    if account.platform == PlatformType.RUBIKA:
        return compute_rubika_runtime_status(
            db,
            account,
            worker_covered=worker_covered,
            worker_stale=worker_stale,
            dispatch_eligible_ids=dispatch_eligible_ids,
        )
    if account.platform == PlatformType.WHATSAPP:
        return compute_whatsapp_runtime_status(
            db, account, worker_covered=worker_covered
        )
    if account.platform in {PlatformType.BALE, PlatformType.TELEGRAM}:
        return compute_token_platform_runtime_status(
            db,
            account,
            credential_type="api_token",
            worker_covered=worker_covered,
        )
    return _make(
        account,
        status=RuntimeStatus.NOT_APPLICABLE,
        reason_code="UNSUPPORTED_PLATFORM",
        auth_state="unknown",
        credential_type=None,
        credential_state="unknown",
        identity_state="unknown",
        worker_state="not_applicable",
        operator_action_code="FIX_CONFIG",
    )


def compute_all_account_runtime_statuses(
    db: Session,
    accounts: list[Account] | None = None,
) -> list[AccountRuntimeStatus]:
    if accounts is None:
        accounts = db.query(Account).order_by(Account.id.asc()).all()

    by_platform: dict[str, list[int]] = {}
    for a in accounts:
        plat = a.platform.value if hasattr(a.platform, "value") else str(a.platform)
        by_platform.setdefault(plat, []).append(int(a.id))

    coverage: dict[tuple[str, int], bool] = {}
    stale: dict[tuple[str, int], bool] = {}
    for plat, ids in by_platform.items():
        batch = batch_worker_coverage_state(platform=plat, account_ids=ids)
        for aid, state in batch.items():
            coverage[(plat, aid)] = state == "fresh"
            stale[(plat, aid)] = state == "stale"

    dispatch_eligible: set[int] | None = None
    try:
        from workers.rubika_worker_discovery import get_dispatch_eligible_rubika_account_ids

        dispatch_eligible = set(get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("event=dispatch_eligible_lookup_failed err=%s", exc)
        dispatch_eligible = None

    out: list[AccountRuntimeStatus] = []
    for account in accounts:
        plat = account.platform.value if hasattr(account.platform, "value") else str(account.platform)
        cov = coverage.get((plat, int(account.id)))
        out.append(
            compute_account_runtime_status(
                db,
                account,
                worker_covered=cov,
                worker_stale=stale.get((plat, int(account.id)), False),
                dispatch_eligible_ids=dispatch_eligible
                if account.platform == PlatformType.RUBIKA
                else None,
            )
        )
    return out


def run_connection_test(
    db: Session,
    account: Account,
    *,
    force_fail: bool = False,
) -> dict[str, Any]:
    """Safe connection/auth verification — never sends, never requests OTP, no session mutation."""
    verified_at = _now_iso()
    if force_fail:
        return {
            "success": False,
            "account_id": int(account.id),
            "platform": account.platform.value,
            "status": RuntimeStatus.CONNECTION_ERROR.value,
            "reason_code": "forced_failure",
            "message": "Connection test failed (forced).",
            "error": "forced_failure",
            "verified_at": verified_at,
        }

    if account.status == AccountStatus.BANNED:
        return {
            "success": False,
            "account_id": int(account.id),
            "platform": account.platform.value,
            "status": RuntimeStatus.DISABLED.value,
            "reason_code": "account_banned",
            "message": "Connection test failed: account is banned.",
            "error": "account_banned",
            "verified_at": verified_at,
        }

    # Coverage snapshot for this account only.
    cov_map = batch_worker_coverage(
        platform=account.platform.value, account_ids=[int(account.id)]
    )
    covered = cov_map.get(int(account.id))

    dispatch_eligible: set[int] | None = None
    if account.platform == PlatformType.RUBIKA:
        try:
            from workers.rubika_worker_discovery import get_dispatch_eligible_rubika_account_ids

            dispatch_eligible = set(
                get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])
            )
        except Exception:  # noqa: BLE001
            dispatch_eligible = None

    runtime = compute_account_runtime_status(
        db,
        account,
        worker_covered=covered,
        dispatch_eligible_ids=dispatch_eligible,
    )

    # Manual-review: do not guess a session via max(id).
    if runtime.runtime_status == RuntimeStatus.MANUAL_REVIEW.value:
        return {
            "success": False,
            "account_id": int(account.id),
            "platform": account.platform.value,
            "status": runtime.runtime_status,
            "reason_code": runtime.reason_code,
            "message": "Manual review required; connection test will not select an ambiguous session.",
            "error": runtime.reason_code.lower(),
            "verified_at": verified_at,
            "runtime_status": runtime.runtime_status,
            "runtime_status_label": runtime.runtime_status_label,
        }

    success_statuses = {
        RuntimeStatus.READY.value,
        RuntimeStatus.AUTHENTICATED_NO_WORKER.value,
    }
    success = runtime.runtime_status in success_statuses
    message = (
        f"Connection verified: {runtime.runtime_status_label}"
        if success
        else f"Connection not ready: {runtime.runtime_status_label} ({runtime.reason_code})"
    )
    return {
        "success": success,
        "account_id": int(account.id),
        "platform": account.platform.value,
        "status": runtime.runtime_status,
        "reason_code": runtime.reason_code,
        "message": message,
        "error": None if success else runtime.reason_code.lower(),
        "verified_at": verified_at,
        "runtime_status": runtime.runtime_status,
        "runtime_status_label": runtime.runtime_status_label,
    }
