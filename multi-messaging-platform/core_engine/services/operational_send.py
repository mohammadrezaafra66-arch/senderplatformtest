"""Controlled operational test send for Phase 9.1+ (single message, audited)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import Account, AccountStatus, PlatformType

_BOT_PLATFORMS = frozenset({
    PlatformType.BALE,
    PlatformType.TELEGRAM,
    PlatformType.RUBIKA,
})

_DEFAULT_TEST_MESSAGE = "پیام تست عملیاتی — Sender Platform (Phase 9)"


@dataclass(frozen=True, slots=True)
class LiveSendPreflightCheck:
    key: str
    passed: bool
    message: str


class OperationalSendError(ValueError):
    """Invalid operational send request."""


def _resolve_recipient(account: Account, recipient: str | None) -> tuple[str, str]:
    """Return (recipient, recipient_type) for a test payload."""
    explicit = (recipient or "").strip()
    platform = account.platform

    if platform in _BOT_PLATFORMS:
        handle = explicit or (account.phone_number or "").strip()
        if not handle:
            raise OperationalSendError(
                "Bot platforms require recipient (chat_id / @username) for test send."
            )
        return handle, "channel_handle"

    if platform == PlatformType.WHATSAPP:
        phone = explicit or (account.phone_number or "").strip()
        if not phone:
            raise OperationalSendError("WhatsApp test send requires a phone number recipient.")
        return phone, "phone_number"

    raise OperationalSendError(f"Unsupported platform: {platform.value}")


def build_test_worker_payload(
    account: Account,
    *,
    message_text: str,
    recipient: str | None = None,
):
    """Build an isolated worker payload for a one-off operational test."""
    from workers.payloads import WorkerPayload

    recipient_value, recipient_type = _resolve_recipient(account, recipient)
    platform = account.platform.value
    test_id = uuid.uuid4().hex[:12]

    metadata: dict[str, Any] = {"source": "operational_send_test", "test_id": test_id}
    if recipient_type == "channel_handle":
        metadata["channel_handle"] = recipient_value

    return WorkerPayload.model_validate(
        {
            "message_id": f"ops-test-{test_id}",
            "campaign_id": f"ops-test-{account.id}",
            "contact_id": f"ops-test-{test_id}",
            "account_id": account.id,
            "platform": platform,
            "recipient": recipient_value,
            "recipient_type": recipient_type,
            "message_text": message_text.strip() or _DEFAULT_TEST_MESSAGE,
            "dedupe_key": f"ops-test-{account.id}-{test_id}",
            "attempt": 1,
            "metadata": metadata,
        }
    )


def _global_live_env_checks() -> list[LiveSendPreflightCheck]:
    settings = get_settings()
    return [
        LiveSendPreflightCheck(
            key="ops_live_send_api_enabled",
            passed=settings.OPS_LIVE_SEND_API_ENABLED,
            message=(
                "OPS_LIVE_SEND_API_ENABLED=true is required for live API test sends."
                if not settings.OPS_LIVE_SEND_API_ENABLED
                else "API live-send gate is enabled."
            ),
        ),
        LiveSendPreflightCheck(
            key="real_message_sending_enabled",
            passed=settings.REAL_MESSAGE_SENDING_ENABLED,
            message=(
                "REAL_MESSAGE_SENDING_ENABLED must be true."
                if not settings.REAL_MESSAGE_SENDING_ENABLED
                else "Real message sending is enabled."
            ),
        ),
        LiveSendPreflightCheck(
            key="channel_connectors_enabled",
            passed=settings.CHANNEL_CONNECTORS_ENABLED,
            message=(
                "CHANNEL_CONNECTORS_ENABLED must be true."
                if not settings.CHANNEL_CONNECTORS_ENABLED
                else "Channel connectors are enabled."
            ),
        ),
        LiveSendPreflightCheck(
            key="dry_run_disabled",
            passed=not settings.DRY_RUN,
            message=(
                "DRY_RUN must be false for live sends."
                if settings.DRY_RUN
                else "DRY_RUN is off."
            ),
        ),
    ]


def build_live_send_preflight(db: Session, account: Account) -> dict[str, Any]:
    """Return a checklist for whether a live test send may proceed."""
    from core_engine.services.account_session_wiring import evaluate_account_session_readiness

    checks: list[LiveSendPreflightCheck] = list(_global_live_env_checks())

    if account.status == AccountStatus.BANNED:
        checks.append(
            LiveSendPreflightCheck(
                key="account_not_banned",
                passed=False,
                message="Account is banned.",
            )
        )
    else:
        checks.append(
            LiveSendPreflightCheck(
                key="account_not_banned",
                passed=True,
                message="Account is not banned.",
            )
        )

    if account.platform == PlatformType.WHATSAPP:
        from core_engine.services.whatsapp_send_guard import whatsapp_send_allowed_sync

        allowed, reason = whatsapp_send_allowed_sync()
        checks.append(
            LiveSendPreflightCheck(
                key="whatsapp_send_allowed",
                passed=allowed,
                message=reason if allowed else f"WhatsApp send blocked: {reason}",
            )
        )

    readiness = evaluate_account_session_readiness(db, account)
    checks.append(
        LiveSendPreflightCheck(
            key="session_ready",
            passed=readiness.ready,
            message=readiness.message,
        )
    )

    ready = all(item.passed for item in checks)
    return {
        "account_id": account.id,
        "platform": account.platform.value,
        "ready_for_live_send": ready,
        "checks": [
            {"key": item.key, "passed": item.passed, "message": item.message}
            for item in checks
        ],
    }


def build_operational_worker_settings(
    *,
    dry_run: bool,
    confirm_live_send: bool,
):
    """Derive worker settings for a test send (dry-run safe by default)."""
    from workers.config import WorkerSettings

    app_settings = get_settings()
    base = WorkerSettings()

    if dry_run:
        return base.model_copy(
            update={
                "DRY_RUN": True,
                "SHADOW_MODE": False,
                "REAL_MESSAGE_SENDING_ENABLED": False,
                "CHANNEL_CONNECTORS_ENABLED": False,
            }
        )

    if not confirm_live_send:
        raise OperationalSendError(
            "Live test send requires confirm_live_send=true when dry_run=false."
        )

    if not app_settings.OPS_LIVE_SEND_API_ENABLED:
        raise OperationalSendError(
            "OPS_LIVE_SEND_API_ENABLED is false. Enable it in .env for live API test sends."
        )
    if not app_settings.REAL_MESSAGE_SENDING_ENABLED:
        raise OperationalSendError(
            "REAL_MESSAGE_SENDING_ENABLED is false. Enable it in .env for live test sends."
        )
    if not app_settings.CHANNEL_CONNECTORS_ENABLED:
        raise OperationalSendError(
            "CHANNEL_CONNECTORS_ENABLED is false. Enable it in .env for live test sends."
        )
    if app_settings.DRY_RUN:
        raise OperationalSendError(
            "DRY_RUN is true in environment. Set DRY_RUN=false for live test sends."
        )

    return base.model_copy(
        update={
            "DRY_RUN": False,
            "SHADOW_MODE": app_settings.SHADOW_MODE,
            "REAL_MESSAGE_SENDING_ENABLED": True,
            "CHANNEL_CONNECTORS_ENABLED": True,
            "WHATSAPP_DELIVERY_MODE": app_settings.WHATSAPP_DELIVERY_MODE,
        }
    )


async def send_account_test_message(
    db: Session,
    account: Account,
    *,
    message_text: str,
    recipient: str | None = None,
    dry_run: bool = True,
    confirm_live_send: bool = False,
) -> dict[str, Any]:
    """Send one operational test message through the worker delivery path."""
    from workers.delivery import deliver_platform_message

    if not dry_run:
        preflight = build_live_send_preflight(db, account)
        if not preflight["ready_for_live_send"]:
            failed = [
                item["message"]
                for item in preflight["checks"]
                if not item["passed"]
            ]
            raise OperationalSendError(
                "Live send preflight failed: " + "; ".join(failed[:3]),
            )

    payload = build_test_worker_payload(
        account,
        message_text=message_text,
        recipient=recipient,
    )
    settings = build_operational_worker_settings(
        dry_run=dry_run,
        confirm_live_send=confirm_live_send,
    )

    app_settings = get_settings()
    delivery_mode = app_settings.WHATSAPP_DELIVERY_MODE.strip().lower()
    if (
        not dry_run
        and account.platform == PlatformType.WHATSAPP
        and (
            (delivery_mode == "web" and app_settings.WHATSAPP_OPS_SEND_VIA_WORKER_QUEUE)
            or delivery_mode == "baileys"
        )
    ):
        from core_engine.services.whatsapp_send_guard import assert_whatsapp_send_allowed

        try:
            await assert_whatsapp_send_allowed()
        except Exception as exc:
            from core_engine.services.delivery_audit import record_whatsapp_delivery_audit
            from core_engine.services.whatsapp_send_guard import WhatsAppSendBlockedError

            if isinstance(exc, WhatsAppSendBlockedError):
                record_whatsapp_delivery_audit(
                    source="ui",
                    account_id=account.id,
                    recipient=payload.recipient,
                    message_id=payload.message_id,
                    message_text=payload.message_text,
                    success=False,
                    status="blocked",
                    error_code="whatsapp_send_disabled",
                    error_message=str(exc),
                    username="ui",
                )
            raise OperationalSendError(str(exc)) from exc

        if delivery_mode == "baileys":
            return await _enqueue_whatsapp_baileys_ops_test_send(db, account, payload)
        return await _enqueue_whatsapp_ops_test_send(account, payload)

    result = await deliver_platform_message(account.platform.value, payload, settings)

    return {
        "account_id": account.id,
        "platform": account.platform.value,
        "dry_run": dry_run,
        "live_send": not dry_run,
        "recipient": payload.recipient,
        "recipient_type": payload.recipient_type,
        "success": result.success,
        "status": result.status,
        "platform_message_id": result.platform_message_id,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "retryable": result.retryable,
        "message": _result_message(result.success, result.status, dry_run),
    }


def _result_message(success: bool, status: str, dry_run: bool) -> str:
    if dry_run and success:
        return "Dry-run test send succeeded (no message delivered to channel)."
    if success and status == "queued":
        return (
            "Live test message queued for the host WhatsApp worker. "
            "Ensure whatsapp_worker_pool_windows.ps1 is running."
        )
    if success:
        return "Live test message accepted by connector."
    return f"Test send failed ({status})."


async def _enqueue_whatsapp_baileys_ops_test_send(
    db: Session,
    account: Account,
    payload,
) -> dict[str, Any]:
    """Push a live ops test job to BullMQ via the Baileys microservice."""
    from core_engine.services.baileys_queue import enqueue_baileys_from_worker_payload

    await enqueue_baileys_from_worker_payload(
        db,
        payload.model_dump(),
        route="ui",
    )

    return {
        "account_id": account.id,
        "platform": account.platform.value,
        "dry_run": False,
        "live_send": True,
        "recipient": payload.recipient,
        "recipient_type": payload.recipient_type,
        "success": True,
        "status": "queued",
        "platform_message_id": None,
        "error_code": None,
        "error_message": None,
        "retryable": False,
        "message": (
            "Live test message queued for Baileys whatsapp-service. "
            "Ensure PM2 workers are running."
        ),
    }


async def _enqueue_whatsapp_ops_test_send(
    account: Account,
    payload,
) -> dict[str, Any]:
    """Push a live ops test payload to Redis for the Windows/host worker pool."""
    import json

    from core_engine.services.redis_client import get_redis_client
    from workers.redis_keys import queue_key

    redis = get_redis_client()
    key = queue_key("whatsapp", account.id)
    raw_payload = json.dumps(payload.model_dump(), ensure_ascii=False)
    await redis.rpush(key, raw_payload)

    return {
        "account_id": account.id,
        "platform": account.platform.value,
        "dry_run": False,
        "live_send": True,
        "recipient": payload.recipient,
        "recipient_type": payload.recipient_type,
        "success": True,
        "status": "queued",
        "platform_message_id": None,
        "error_code": None,
        "error_message": None,
        "retryable": False,
        "message": _result_message(True, "queued", dry_run=False),
    }


def operational_send_capabilities() -> dict[str, bool]:
    """Expose whether live operational sends are allowed by current env."""
    settings = get_settings()
    live_env_ok = (
        settings.OPS_LIVE_SEND_API_ENABLED
        and settings.REAL_MESSAGE_SENDING_ENABLED
        and settings.CHANNEL_CONNECTORS_ENABLED
        and not settings.DRY_RUN
    )
    return {
        "dry_run_default": True,
        "ops_live_send_api_enabled": settings.OPS_LIVE_SEND_API_ENABLED,
        "live_send_allowed": live_env_ok,
        "real_message_sending_enabled": settings.REAL_MESSAGE_SENDING_ENABLED,
        "channel_connectors_enabled": settings.CHANNEL_CONNECTORS_ENABLED,
        "dry_run_env": settings.DRY_RUN,
    }
