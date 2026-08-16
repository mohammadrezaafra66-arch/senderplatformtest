"""Unified account session wiring for all messaging channels (Phase 8.6 + Rubika Phase 1)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import Account, AccountStatus, ChannelSession, PlatformType, SessionType
from core_engine.services.crypto import SessionDecryptionError
from core_engine.services.rubika_mode import (
    RUBIKA_MODE_BOT_API,
    RUBIKA_MODE_USER_ACCOUNT,
    is_rubika_user_account_enabled,
    resolve_rubika_delivery_mode,
    rubika_required_session_type,
)
from core_engine.services.safety_guard import get_safety_status
from core_engine.services.session_storage import (
    load_channel_session_plaintext,
    store_channel_session,
)
from core_engine.services.whatsapp_web_session import build_whatsapp_web_status

_API_TOKEN_PLATFORMS = frozenset({
    PlatformType.BALE,
    PlatformType.TELEGRAM,
    PlatformType.RUBIKA,
})


# Structured readiness codes (uppercase). `error` mirrors historical snake_case API field.
READY = "READY"
ACCOUNT_MISSING = "ACCOUNT_MISSING"
ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
ACCOUNT_BANNED = "ACCOUNT_BANNED"
ACCOUNT_REQUIRES_LOGIN = "ACCOUNT_REQUIRES_LOGIN"
ACCOUNT_IDENTIFIER_MISSING = "ACCOUNT_IDENTIFIER_MISSING"
WRONG_PLATFORM = "WRONG_PLATFORM"
SESSION_MISSING = "SESSION_MISSING"
SESSION_INVALID = "SESSION_INVALID"
SESSION_DECRYPT_FAILED = "SESSION_DECRYPT_FAILED"
DELIVERY_MODE_DISABLED = "DELIVERY_MODE_DISABLED"
USER_ACCOUNT_DISABLED = "USER_ACCOUNT_DISABLED"
CONFIG_INVALID = "CONFIG_INVALID"
EVOLUTION_NOT_CONNECTED = "EVOLUTION_NOT_CONNECTED"
WHATSAPP_WEB_NOT_LINKED = "WHATSAPP_WEB_NOT_LINKED"


def _code_to_error(code: str | None) -> str | None:
    if code is None or code == READY:
        return None
    return code.lower()


@dataclass(frozen=True, slots=True)
class SessionReadiness:
    ready: bool
    message: str
    error: str | None = None
    code: str = READY
    session_type: str | None = None
    delivery_mode: str | None = None

    @staticmethod
    def make(
        *,
        ready: bool,
        message: str,
        code: str,
        session_type: SessionType | str | None = None,
        delivery_mode: str | None = None,
    ) -> "SessionReadiness":
        st = session_type.value if isinstance(session_type, SessionType) else session_type
        return SessionReadiness(
            ready=ready,
            message=message,
            error=_code_to_error(code) if not ready else None,
            code=code if not ready else READY,
            session_type=st,
            delivery_mode=delivery_mode,
        )


def resolve_whatsapp_delivery_mode() -> str:
    mode = get_settings().WHATSAPP_DELIVERY_MODE.strip().lower()
    return mode if mode in {"web", "cloud_api"} else "web"


def required_session_type(
    platform: PlatformType,
    *,
    whatsapp_delivery_mode: str | None = None,
    rubika_delivery_mode: str | None = None,
) -> SessionType:
    """Return the session type workers expect for a platform.

    Rubika mapping is centralized in ``rubika_required_session_type`` —
    invalid delivery modes raise ValueError (no silent fallback).
    """
    if platform == PlatformType.WHATSAPP:
        mode = (whatsapp_delivery_mode or resolve_whatsapp_delivery_mode()).strip().lower()
        if mode == "cloud_api":
            return SessionType.API_TOKEN
        return SessionType.BROWSER_PROFILE
    if platform == PlatformType.RUBIKA:
        mode = resolve_rubika_delivery_mode(rubika_delivery_mode=rubika_delivery_mode)
        return rubika_required_session_type(mode)
    if platform in _API_TOKEN_PLATFORMS:
        return SessionType.API_TOKEN
    raise ValueError(f"Unsupported platform for session wiring: {platform.value}")


def _latest_session_row(
    db: Session,
    account_id: int,
    session_type: SessionType,
) -> ChannelSession | None:
    """Deterministic current session: highest ChannelSession.id wins."""
    return (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == account_id,
            ChannelSession.session_type == session_type,
        )
        .order_by(ChannelSession.id.desc())
        .first()
    )


def has_encrypted_session(
    db: Session,
    account_id: int,
    session_type: SessionType,
) -> bool:
    row = _latest_session_row(db, account_id, session_type)
    return row is not None and bool(row.ciphertext)


def _validate_session_payload(
    platform: PlatformType,
    payload: str,
    *,
    rubika_delivery_mode: str | None = None,
) -> str:
    text = payload.strip()
    if not text:
        raise ValueError("session_payload cannot be empty.")

    if platform == PlatformType.WHATSAPP:
        if not text.startswith("{"):
            raise ValueError(
                "WhatsApp Cloud API session must be JSON with access_token and phone_number_id."
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("WhatsApp session JSON is invalid.") from exc
        if not isinstance(data, dict):
            raise ValueError("WhatsApp session JSON must be an object.")
        access_token = str(data.get("access_token") or data.get("token") or "").strip()
        phone_number_id = str(data.get("phone_number_id") or "").strip()
        if not access_token or not phone_number_id:
            raise ValueError(
                "WhatsApp session JSON must include access_token and phone_number_id."
            )
        return json.dumps(
            {"access_token": access_token, "phone_number_id": phone_number_id},
            ensure_ascii=False,
        )

    if platform == PlatformType.RUBIKA:
        mode = resolve_rubika_delivery_mode(rubika_delivery_mode=rubika_delivery_mode)
        if mode == RUBIKA_MODE_USER_ACCOUNT:
            from core_engine.services.rubika_user_session import parse_session_envelope

            try:
                parse_session_envelope(text)
            except ValueError as exc:
                raise ValueError(
                    f"Rubika user-account session envelope invalid: {exc}"
                ) from exc
            return text

    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Session JSON is invalid.") from exc
        if not isinstance(data, dict):
            raise ValueError("Session JSON must be an object.")
        for key in ("bot_token", "token", "api_token"):
            if str(data.get(key) or "").strip():
                return text
        raise ValueError("Session JSON must include bot_token, token, or api_token.")

    return text


def register_api_token_session(
    db: Session,
    *,
    account: Account,
    session_payload: str,
) -> ChannelSession:
    """Store encrypted API token session for bot platforms or WhatsApp Cloud API."""
    if account.platform == PlatformType.WHATSAPP:
        mode = resolve_whatsapp_delivery_mode()
        if mode != "cloud_api":
            raise ValueError(
                "API token registration is only for WhatsApp when WHATSAPP_DELIVERY_MODE=cloud_api."
            )
    elif account.platform == PlatformType.RUBIKA:
        from core_engine.services.rubika_mode import assert_rubika_bot_api_registration_allowed

        assert_rubika_bot_api_registration_allowed()
    elif account.platform not in _API_TOKEN_PLATFORMS:
        raise ValueError(
            f"Platform {account.platform.value} does not use API token sessions."
        )

    normalized = _validate_session_payload(account.platform, session_payload)
    row = store_channel_session(
        db,
        account_id=account.id,
        session_type=SessionType.API_TOKEN,
        plaintext=normalized,
    )
    if account.status == AccountStatus.REQUIRES_LOGIN:
        account.status = AccountStatus.ACTIVE
    db.flush()
    return row


def build_account_session_status(
    db: Session,
    account: Account,
    *,
    whatsapp_delivery_mode: str | None = None,
) -> dict[str, Any]:
    """Summarize session readiness for API/UI (no live channel probe)."""
    mode = whatsapp_delivery_mode or resolve_whatsapp_delivery_mode()
    rubika_mode: str | None = None
    if account.platform == PlatformType.RUBIKA:
        try:
            rubika_mode = resolve_rubika_delivery_mode()
        except ValueError:
            rubika_mode = None

    if account.platform == PlatformType.RUBIKA and rubika_mode is None:
        session_type = SessionType.API_TOKEN
    else:
        session_type = required_session_type(
            account.platform,
            whatsapp_delivery_mode=mode,
            rubika_delivery_mode=rubika_mode,
        )

    try:
        readiness = evaluate_account_session_readiness(
            db, account, whatsapp_delivery_mode=mode
        )
    except ValueError as exc:
        readiness = SessionReadiness.make(
            ready=False,
            message=str(exc),
            code=CONFIG_INVALID,
            session_type=session_type,
            delivery_mode=rubika_mode,
        )

    base: dict[str, Any] = {
        "account_id": account.id,
        "platform": account.platform.value,
        "account_status": account.status.value,
        "session_type": readiness.session_type or session_type.value,
        "session_registered": (
            has_encrypted_session(db, account.id, session_type)
            if account.platform != PlatformType.RUBIKA or rubika_mode is not None
            else False
        ),
        "ready_for_delivery": readiness.ready,
        "message": readiness.message,
        "error": readiness.error,
        "code": readiness.code,
        "delivery_mode": readiness.delivery_mode,
    }

    if account.platform == PlatformType.WHATSAPP and mode == "web":
        try:
            wa_status = build_whatsapp_web_status(db, account.id)
        except SessionDecryptionError:
            base["delivery_mode"] = "web"
            base["message"] = "Stored session could not be decrypted."
            base["error"] = "session_decrypt_failed"
            base["code"] = SESSION_DECRYPT_FAILED
            return base
        base.update(
            {
                "delivery_mode": "web",
                "linked": wa_status["linked"],
                "needs_qr": wa_status["needs_qr"],
                "profile_exists": wa_status["profile_exists"],
                "profile_dir": wa_status["profile_dir"],
                "linked_at": wa_status.get("linked_at"),
            }
        )
        base["session_registered"] = bool(wa_status["session_registered"])
        base["ready_for_delivery"] = bool(
            wa_status["profile_exists"] and wa_status["linked"] and readiness.ready
        )
        if not base["ready_for_delivery"]:
            base["message"] = str(wa_status["message"])
    elif account.platform == PlatformType.WHATSAPP:
        base["delivery_mode"] = "cloud_api"
    elif account.platform == PlatformType.RUBIKA:
        base["delivery_mode"] = readiness.delivery_mode or rubika_mode

    return base


def evaluate_account_session_readiness(
    db: Session,
    account: Account | None,
    *,
    whatsapp_delivery_mode: str | None = None,
    rubika_delivery_mode: str | None = None,
    rubika_user_account_enabled: bool | None = None,
) -> SessionReadiness:
    """Check whether an account has the credentials workers need."""
    if account is None:
        return SessionReadiness.make(
            ready=False,
            message="Account not found.",
            code=ACCOUNT_MISSING,
        )

    rubika_mode: str | None = None
    if account.platform == PlatformType.RUBIKA:
        try:
            rubika_mode = resolve_rubika_delivery_mode(
                rubika_delivery_mode=rubika_delivery_mode
            )
        except ValueError as exc:
            return SessionReadiness.make(
                ready=False,
                message=str(exc),
                code=CONFIG_INVALID,
            )

        if rubika_mode == RUBIKA_MODE_USER_ACCOUNT:
            enabled = (
                is_rubika_user_account_enabled()
                if rubika_user_account_enabled is None
                else bool(rubika_user_account_enabled)
            )
            if not enabled:
                return SessionReadiness.make(
                    ready=False,
                    message=(
                        "Rubika user-account delivery is disabled "
                        "(RUBIKA_USER_ACCOUNT_ENABLED=false)."
                    ),
                    code=USER_ACCOUNT_DISABLED,
                    session_type=SessionType.RUBIKA_SESSION,
                    delivery_mode=rubika_mode,
                )

    session_type = required_session_type(
        account.platform,
        whatsapp_delivery_mode=whatsapp_delivery_mode,
        rubika_delivery_mode=rubika_mode,
    )
    delivery_mode = rubika_mode
    if account.platform == PlatformType.WHATSAPP:
        delivery_mode = whatsapp_delivery_mode or resolve_whatsapp_delivery_mode()

    if account.status == AccountStatus.BANNED:
        return SessionReadiness.make(
            ready=False,
            message="Account is banned.",
            code=ACCOUNT_BANNED,
            session_type=session_type,
            delivery_mode=delivery_mode,
        )
    if account.status == AccountStatus.REQUIRES_LOGIN:
        return SessionReadiness.make(
            ready=False,
            message="Account requires login or session registration.",
            code=ACCOUNT_REQUIRES_LOGIN,
            session_type=session_type,
            delivery_mode=delivery_mode,
        )
    if account.status == AccountStatus.RESTING:
        return SessionReadiness.make(
            ready=False,
            message="Account is resting (temporarily disabled for delivery).",
            code=ACCOUNT_DISABLED,
            session_type=session_type,
            delivery_mode=delivery_mode,
        )
    if not account.phone_number:
        return SessionReadiness.make(
            ready=False,
            message="Missing account_identifier.",
            code=ACCOUNT_IDENTIFIER_MISSING,
            session_type=session_type,
            delivery_mode=delivery_mode,
        )

    mode = whatsapp_delivery_mode or resolve_whatsapp_delivery_mode()

    raw_delivery_mode = get_settings().WHATSAPP_DELIVERY_MODE.strip().lower()
    if account.platform == PlatformType.WHATSAPP and raw_delivery_mode == "evolution":
        evolution_session = (
            db.query(ChannelSession)
            .filter(
                ChannelSession.account_id == account.id,
                ChannelSession.session_type == SessionType.EVOLUTION_INSTANCE,
            )
            .order_by(ChannelSession.id.desc())
            .first()
        )
        if (
            evolution_session is not None
            and (evolution_session.evolution_status or "").strip().lower() == "open"
        ):
            return SessionReadiness.make(
                ready=True,
                message="Evolution instance connected",
                code=READY,
                session_type=SessionType.EVOLUTION_INSTANCE,
                delivery_mode="evolution",
            )
        return SessionReadiness.make(
            ready=False,
            message="Evolution instance is not connected.",
            code=EVOLUTION_NOT_CONNECTED,
            session_type=SessionType.EVOLUTION_INSTANCE,
            delivery_mode="evolution",
        )

    if account.platform == PlatformType.WHATSAPP and mode == "web":
        try:
            wa_status = build_whatsapp_web_status(db, account.id)
        except SessionDecryptionError:
            return SessionReadiness.make(
                ready=False,
                message="Stored session could not be decrypted.",
                code=SESSION_DECRYPT_FAILED,
                session_type=session_type,
                delivery_mode="web",
            )
        if wa_status["linked"] and wa_status["profile_exists"]:
            return SessionReadiness.make(
                ready=True,
                message="WhatsApp Web session is linked and ready.",
                code=READY,
                session_type=session_type,
                delivery_mode="web",
            )
        return SessionReadiness.make(
            ready=False,
            message=str(wa_status["message"]),
            code=WHATSAPP_WEB_NOT_LINKED,
            session_type=session_type,
            delivery_mode="web",
        )

    if not has_encrypted_session(db, account.id, session_type):
        label = session_type.value.replace("_", " ")
        return SessionReadiness.make(
            ready=False,
            message=f"No {label} session registered for this account.",
            code=SESSION_MISSING,
            session_type=session_type,
            delivery_mode=delivery_mode,
        )

    row = _latest_session_row(db, account.id, session_type)
    assert row is not None
    try:
        plaintext = load_channel_session_plaintext(row)
    except Exception:
        return SessionReadiness.make(
            ready=False,
            message="Stored session could not be decrypted.",
            code=SESSION_DECRYPT_FAILED,
            session_type=session_type,
            delivery_mode=delivery_mode,
        )

    try:
        _validate_session_payload(
            account.platform,
            plaintext.decode("utf-8"),
            rubika_delivery_mode=rubika_mode,
        )
    except ValueError as exc:
        return SessionReadiness.make(
            ready=False,
            message=str(exc),
            code=SESSION_INVALID,
            session_type=session_type,
            delivery_mode=delivery_mode,
        )

    return SessionReadiness.make(
        ready=True,
        message="Session is registered and looks valid.",
        code=READY,
        session_type=session_type,
        delivery_mode=delivery_mode or (
            RUBIKA_MODE_BOT_API if account.platform == PlatformType.RUBIKA else None
        ),
    )


def build_deploy_readiness(db: Session) -> dict[str, Any]:
    """Operational checklist for Phase 8 deploy (accounts + safety flags)."""
    from core_engine.services.operational_send import operational_send_capabilities

    settings = get_settings()
    mode = resolve_whatsapp_delivery_mode()
    accounts = db.query(Account).order_by(Account.id.asc()).all()

    account_items: list[dict[str, Any]] = []
    active_ready = 0
    active_total = 0

    for account in accounts:
        status = build_account_session_status(db, account, whatsapp_delivery_mode=mode)
        if account.status == AccountStatus.ACTIVE:
            active_total += 1
            if status["ready_for_delivery"]:
                active_ready += 1
        account_items.append(status)

    try:
        rubika_mode = resolve_rubika_delivery_mode()
    except ValueError:
        rubika_mode = settings.RUBIKA_DELIVERY_MODE

    worker_services = [
        {"name": "bale_worker", "platform": "bale", "mode": "single_account"},
        {"name": "telegram_worker", "platform": "telegram", "mode": "single_account"},
        {
            "name": "rubika_worker",
            "platform": "rubika",
            "mode": "single_account",
            "enabled_when": "RUBIKA_DELIVERY_MODE=bot_api",
            "delivery_mode": rubika_mode,
        },
        {
            "name": "rubika_user_pool",
            "platform": "rubika",
            "mode": "user_account_pool",
            "enabled_when": (
                "RUBIKA_DELIVERY_MODE=user_account و RUBIKA_USER_ACCOUNT_ENABLED=true"
            ),
            "delivery_mode": rubika_mode,
            "user_account_enabled": is_rubika_user_account_enabled(),
        },
        {
            "name": "whatsapp_worker",
            "platform": "whatsapp",
            "mode": "cloud_api",
            "enabled_when": "WHATSAPP_DELIVERY_MODE=cloud_api",
        },
        {
            "name": "whatsapp_worker_pool",
            "platform": "whatsapp",
            "mode": "web",
            "enabled_when": "WHATSAPP_DELIVERY_MODE=web",
        },
    ]

    return {
        "phase": "9.2",
        "safety": get_safety_status(),
        "dry_run": settings.DRY_RUN,
        "shadow_mode": settings.SHADOW_MODE,
        "whatsapp_delivery_mode": mode,
        "rubika_delivery_mode": rubika_mode,
        "rubika_user_account_enabled": is_rubika_user_account_enabled(),
        "operational_send": operational_send_capabilities(),
        "worker_services": worker_services,
        "accounts_total": len(accounts),
        "active_accounts_total": active_total,
        "active_accounts_ready": active_ready,
        "all_active_accounts_ready": active_total == 0 or active_ready == active_total,
        "accounts": account_items,
    }
