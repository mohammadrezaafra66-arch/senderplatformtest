"""Worker delivery modes: dry-run, shadow, and live (connector) dispatch."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from workers.payloads import WorkerPayload, WorkerResult

if TYPE_CHECKING:
    from workers.config import WorkerSettings

from workers.connectors.bale import deliver_bale_live
from workers.connectors.rubika import deliver_rubika_live
from workers.connectors.telegram import deliver_telegram_live
from workers.connectors.telegram_mtproto import deliver_telegram_mtproto_live
from workers.connectors.whatsapp import deliver_whatsapp_cloud_live
from workers.connectors.whatsapp_web import deliver_whatsapp_web_live


def _synthetic_platform_message_id(prefix: str, payload: WorkerPayload) -> str:
    return f"{prefix}-{payload.dedupe_key}-{uuid.uuid4().hex[:12]}"


async def deliver_platform_message(
    platform: str,
    payload: WorkerPayload,
    settings: WorkerSettings,
) -> WorkerResult:
    """Send (or simulate) a message according to worker safety settings."""
    if settings.DRY_RUN:
        return WorkerResult(
            success=True,
            status="dry_run",
            platform_message_id=_synthetic_platform_message_id("dry", payload),
            retryable=False,
        )

    if settings.SHADOW_MODE:
        if not settings.SHADOW_PHONE_NUMBER:
            return WorkerResult(
                success=False,
                status="failed_permanent",
                error_code="shadow_phone_missing",
                error_message="SHADOW_MODE is enabled but SHADOW_PHONE_NUMBER is empty.",
                retryable=False,
            )
        return WorkerResult(
            success=True,
            status="shadow_sent",
            platform_message_id=_synthetic_platform_message_id("shadow", payload),
            retryable=False,
        )

    if not settings.REAL_MESSAGE_SENDING_ENABLED:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="real_send_disabled",
            error_message="REAL_MESSAGE_SENDING_ENABLED is false.",
            retryable=False,
        )

    if not settings.CHANNEL_CONNECTORS_ENABLED:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="connectors_disabled",
            error_message=(
                f"Live {platform} connector is not enabled "
                "(CHANNEL_CONNECTORS_ENABLED=false)."
            ),
            retryable=False,
        )

    if platform == "bale":
        if settings.BALE_DELIVERY_MODE == "user_account":
            if not settings.BALE_ENABLE_USER_ACCOUNT:
                return WorkerResult(error_code="bale_user_account_disabled", success=False, status="failed_permanent", retryable=False)
            from workers.connectors.bale_user import deliver_bale_user_live
            return await deliver_bale_user_live(payload, settings)
        return await deliver_bale_live(payload, settings)

    if platform == "telegram":
        if getattr(settings, "TELEGRAM_DELIVERY_MODE", "bot_api") == "mtproto_account":
            if not getattr(settings, "TELEGRAM_ENABLE_MTPROTO", False):
                return WorkerResult(
                    success=False,
                    status="failed_permanent",
                    error_code="telegram_mtproto_disabled",
                    error_message="TELEGRAM_ENABLE_MTPROTO is false.",
                    retryable=False,
                )
            return await deliver_telegram_mtproto_live(payload, settings)
        return await deliver_telegram_live(payload, settings)

    if platform == "whatsapp":
        mode = settings.WHATSAPP_DELIVERY_MODE.strip().lower()
        if mode == "evolution":
            from workers.connectors.whatsapp_evolution import (
                deliver_whatsapp_evolution_live,
            )

            return await deliver_whatsapp_evolution_live(payload, settings)
        if mode == "web":
            return await deliver_whatsapp_web_live(payload, settings)
        return await deliver_whatsapp_cloud_live(payload, settings)

    if platform == "rubika":
        return await deliver_rubika_live(payload, settings)

    return WorkerResult(
        success=False,
        status="placeholder_not_implemented",
        error_code="not_implemented",
        error_message=f"Live {platform} delivery is not implemented yet.",
        retryable=False,
    )
