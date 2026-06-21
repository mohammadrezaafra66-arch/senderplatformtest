"""Unified account session wiring for all messaging channels (Phase 8.6)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import Account, AccountStatus, ChannelSession, PlatformType, SessionType
from core_engine.services.crypto import SessionDecryptionError
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


@dataclass(frozen=True, slots=True)
class SessionReadiness:
    ready: bool
    message: str
    error: str | None = None


def resolve_whatsapp_delivery_mode() -> str:
    mode = get_settings().WHATSAPP_DELIVERY_MODE.strip().lower()
    return mode if mode in {"web", "cloud_api"} else "web"


def required_session_type(
    platform: PlatformType,
    *,
    whatsapp_delivery_mode: str | None = None,
) -> SessionType:
    """Return the session type workers expect for a platform."""
    if platform == PlatformType.WHATSAPP:
        mode = (whatsapp_delivery_mode or resolve_whatsapp_delivery_mode()).strip().lower()
        if mode == "cloud_api":
            return SessionType.API_TOKEN
        return SessionType.BROWSER_PROFILE
    if platform in _API_TOKEN_PLATFORMS:
        return SessionType.API_TOKEN
    raise ValueError(f"Unsupported platform for session wiring: {platform.value}")


def _latest_session_row(
    db: Session,
    account_id: int,
    session_type: SessionType,
) -> ChannelSession | None:
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


def _validate_session_payload(platform: PlatformType, payload: str) -> str:
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
    session_type = required_session_type(account.platform, whatsapp_delivery_mode=mode)
    readiness = evaluate_account_session_readiness(db, account, whatsapp_delivery_mode=mode)

    base: dict[str, Any] = {
        "account_id": account.id,
        "platform": account.platform.value,
        "account_status": account.status.value,
        "session_type": session_type.value,
        "session_registered": has_encrypted_session(db, account.id, session_type),
        "ready_for_delivery": readiness.ready,
        "message": readiness.message,
        "error": readiness.error,
    }

    if account.platform == PlatformType.WHATSAPP and mode == "web":
        try:
            wa_status = build_whatsapp_web_status(db, account.id)
        except SessionDecryptionError:
            base["delivery_mode"] = "web"
            base["message"] = "Stored session could not be decrypted."
            base["error"] = "session_decrypt_failed"
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
    else:
        base["delivery_mode"] = None

    return base


def evaluate_account_session_readiness(
    db: Session,
    account: Account,
    *,
    whatsapp_delivery_mode: str | None = None,
) -> SessionReadiness:
    """Check whether an account has the credentials workers need."""
    if account.status == AccountStatus.BANNED:
        return SessionReadiness(False, "Account is banned.", error="account_banned")
    if account.status == AccountStatus.REQUIRES_LOGIN:
        return SessionReadiness(
            False,
            "Account requires login or session registration.",
            error="account_requires_login",
        )
    if not account.phone_number:
        return SessionReadiness(
            False,
            "Missing account_identifier.",
            error="account_identifier_missing",
        )

    mode = whatsapp_delivery_mode or resolve_whatsapp_delivery_mode()
    session_type = required_session_type(account.platform, whatsapp_delivery_mode=mode)

    if account.platform == PlatformType.WHATSAPP and mode == "web":
        try:
            wa_status = build_whatsapp_web_status(db, account.id)
        except SessionDecryptionError:
            return SessionReadiness(
                False,
                "Stored session could not be decrypted.",
                error="session_decrypt_failed",
            )
        if wa_status["linked"] and wa_status["profile_exists"]:
            return SessionReadiness(True, "WhatsApp Web session is linked and ready.")
        return SessionReadiness(
            False,
            str(wa_status["message"]),
            error="whatsapp_web_not_linked",
        )

    if not has_encrypted_session(db, account.id, session_type):
        label = session_type.value.replace("_", " ")
        return SessionReadiness(
            False,
            f"No {label} session registered for this account.",
            error="session_missing",
        )

    # Verify ciphertext decrypts (catches corrupt sessions early).
    row = _latest_session_row(db, account.id, session_type)
    assert row is not None
    try:
        plaintext = load_channel_session_plaintext(row)
    except Exception:
        return SessionReadiness(
            False,
            "Stored session could not be decrypted.",
            error="session_decrypt_failed",
        )

    try:
        _validate_session_payload(account.platform, plaintext.decode("utf-8"))
    except ValueError as exc:
        return SessionReadiness(False, str(exc), error="session_invalid")

    return SessionReadiness(True, "Session is registered and looks valid.")


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

    worker_services = [
        {"name": "bale_worker", "platform": "bale", "mode": "single_account"},
        {"name": "telegram_worker", "platform": "telegram", "mode": "single_account"},
        {"name": "rubika_worker", "platform": "rubika", "mode": "single_account"},
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
        "operational_send": operational_send_capabilities(),
        "worker_services": worker_services,
        "accounts_total": len(accounts),
        "active_accounts_total": active_total,
        "active_accounts_ready": active_ready,
        "all_active_accounts_ready": active_total == 0 or active_ready == active_total,
        "accounts": account_items,
    }
