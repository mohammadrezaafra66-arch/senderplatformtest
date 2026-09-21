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
from workers.connectors.whatsapp import deliver_whatsapp_cloud_live
from workers.connectors.whatsapp_web import deliver_whatsapp_web_live


def _synthetic_platform_message_id(prefix: str, payload: WorkerPayload) -> str:
    return f"{prefix}-{payload.dedupe_key}-{uuid.uuid4().hex[:12]}"


async def deliver_platform_message(
    platform: str,
    payload: WorkerPayload,
    settings: WorkerSettings,
) -> WorkerResult:
    """Send (or simulate) a message according to worker safety settings.

    Product campaigns refresh AfraKala before the text is handed to a connector.
    A blocked refresh never falls through to the prepare-time price.
    """
    from core_engine.services.product_feed.send_time_refresh import (
        apply_send_time_product_refresh,
    )

    refresh = apply_send_time_product_refresh(payload)
    if refresh.blocked:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code=refresh.reason_code,
            error_message=refresh.reason_message,
            retryable=refresh.retryable,
        )
    if refresh.refreshed and refresh.message_text:
        payload.message_text = refresh.message_text
        payload.metadata = refresh.payload.get("metadata") or payload.metadata

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

    # Canonical transport kill switch (Phase 7). OFF → connectors never run.
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

    # Race-safe archive guard: re-read DB immediately before provider send.
    from workers.db import check_archive_blocks_provider_send

    campaign_id_for_archive_check = payload.campaign_id

    if (payload.metadata or {}).get("source") == "operational_send_test":
        # Operational test is not backed by a Campaign row.
        # Keep account archive protection, skip campaign archive lookup.
        campaign_id_for_archive_check = None

    archive_block = check_archive_blocks_provider_send(
        account_id=payload.account_id,
        campaign_id=campaign_id_for_archive_check,
    )

    if archive_block:
        code = archive_block.upper()
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code=archive_block,
            error_message=f"{code}: entity archived; provider send refused.",
            retryable=False,
        )
    if refresh.refreshed:
        # The connector may accept this request even if the local result is lost.
        # A later retry must replay this text instead of rendering a new price.
        payload.metadata = {
            **(payload.metadata or {}),
            "external_send_submitted": True,
        }

    if platform == "bale":
        return await deliver_bale_live(payload, settings)

    if platform == "telegram":
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
        from core_engine.services.message_observability import (
            apply_submission_boundary,
            classify_rubika_connector_result,
        )

        mode = settings.RUBIKA_DELIVERY_MODE.strip().lower()
        if mode == "user_account":
            if not settings.RUBIKA_USER_ACCOUNT_ENABLED:
                return WorkerResult(
                    success=False,
                    status="failed_permanent",
                    error_code="rubika_user_account_disabled",
                    error_message=(
                        "RUBIKA_DELIVERY_MODE=user_account but "
                        "RUBIKA_USER_ACCOUNT_ENABLED=false."
                    ),
                    retryable=False,
                )
            from workers.connectors.rubika_user import deliver_rubika_user_live

            result = await deliver_rubika_user_live(payload, settings)
        else:
            result = await deliver_rubika_live(payload, settings)
        submitted = bool((payload.metadata or {}).get("external_send_submitted"))
        return apply_submission_boundary(
            classify_rubika_connector_result(result),
            submitted=submitted,
        )

    return WorkerResult(
        success=False,
        status="placeholder_not_implemented",
        error_code="not_implemented",
        error_message=f"Live {platform} delivery is not implemented yet.",
        retryable=False,
    )
