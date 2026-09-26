"""شروع/توقف کمپین — وضعیت DB + کلید pause در Redis."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core_engine.models import Campaign, CampaignStatus, PlatformType
from core_engine.services.campaign_preflight import (
    CAMPAIGN_CAPACITY_UNKNOWN,
    CAMPAIGN_DEPENDENCY_ERROR,
    CONTROLLED_PRODUCTION_APPROVAL_REQUIRED,
    evaluate_campaign_send_preflight,
    log_campaign_safety_event,
)
from core_engine.services.queue_bridge import push_staged_items_to_worker_queue
from core_engine.services.redis_client import get_redis_client, ping_redis
from core_engine.config import get_settings
from workers.redis_flags import (
    clear_campaign_pause_flag,
    set_campaign_pause_flag,
)

logger = logging.getLogger(__name__)

_STARTABLE_STATUSES = {
    CampaignStatus.DRAFT.value,
    CampaignStatus.PREPARED.value,
    CampaignStatus.PAUSED.value,
    CampaignStatus.RUNNING.value,
}
_STOPPABLE_STATUSES = {
    CampaignStatus.RUNNING.value,
    CampaignStatus.PAUSED.value,
    CampaignStatus.PREPARED.value,
}


class CampaignControlError(Exception):
    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def _require_redis() -> None:
    if not await ping_redis():
        raise CampaignControlError("Redis is unavailable", status_code=503)


async def clear_campaign_pause(campaign_id: int) -> None:
    await _require_redis()
    client = get_redis_client()
    try:
        await clear_campaign_pause_flag(client, campaign_id)
    except Exception as exc:
        raise CampaignControlError("Failed to clear campaign pause in Redis", status_code=503) from exc


async def set_campaign_pause(campaign_id: int) -> None:
    await _require_redis()
    client = get_redis_client()
    try:
        await set_campaign_pause_flag(client, campaign_id, paused=True)
    except Exception as exc:
        raise CampaignControlError("Failed to set campaign pause in Redis", status_code=503) from exc


def _auto_prepare(db: Session, campaign_id: int) -> None:
    """Render and stage messages before start — delegates to auto-prepare service."""
    from core_engine.services.campaign_auto_prepare import try_auto_prepare_campaign

    result = try_auto_prepare_campaign(db, campaign_id, trigger="start", force=True)
    if result.error_code and result.error_code not in {
        "no_contacts",
        "NO_CONTACTS",
        "empty_campaign",
        "EMPTY_CAMPAIGN",
        "template_text_empty",
        "TEMPLATE_TEXT_EMPTY",
    }:
        raise HTTPException(
            status_code=400,
            detail={
                "code": result.error_code,
                "message": result.error_message or "Prepare failed.",
            },
        )
    if result.blockers and not result.prepared and not result.skipped:
        blocker = result.blockers[0]
        raise HTTPException(status_code=400, detail=blocker)

    if result.prepare_result is not None:
        logger.info(
            "auto-prepare campaign=%s staged=%s ready=%s already_staged=%s",
            campaign_id,
            result.prepare_result.staged_count,
            result.prepare_result.ready_count,
            result.prepare_result.already_staged_count,
        )


async def start_campaign(
    db: Session,
    campaign: Campaign,
    *,
    trigger_bridge: bool = True,
    confirm_controlled_production: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    from core_engine.services.archive import require_campaign_not_archived

    require_campaign_not_archived(campaign)

    if campaign.status not in _STARTABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Campaign cannot be started from status '{campaign.status}'.",
        )

    from core_engine.services.campaign_send_safety import pilot_blocks_start

    if pilot_blocks_start(db, campaign.id):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PILOT_RESUME_REQUIRED",
                "message": "ادامه این کمپین بعد از سقف آزمایشی فقط با تأیید مدیر ممکن است.",
            },
        )

    from core_engine.services.campaign_production_guards import (
        campaign_cap_view,
        controlled_production_enabled,
    )

    cp_enabled = bool(controlled_production_enabled())
    log_campaign_safety_event(
        "campaign_start_received",
        campaign_id=campaign.id,
        code=None,
        extra={
            "request_id": request_id,
            "controlled_confirmation_received": bool(confirm_controlled_production),
            "controlled_production_enabled": cp_enabled,
            "platform": getattr(campaign.platform, "value", str(campaign.platform)),
        },
    )

    if (
        cp_enabled
        and campaign.platform == PlatformType.RUBIKA
        and not confirm_controlled_production
    ):
        cap_view = campaign_cap_view(campaign)
        raise HTTPException(
            status_code=409,
            detail={
                "code": CONTROLLED_PRODUCTION_APPROVAL_REQUIRED,
                "message": (
                    "Controlled production mode requires explicit operator approval "
                    "(confirm_controlled_production=true) before start."
                ),
                **cap_view,
                "campaign_max_total_messages": cap_view["effective_cap"],
                "request_id": request_id,
            },
        )

    already_running = campaign.status == CampaignStatus.RUNNING.value

    # Must run *before* the flip to RUNNING below: on success
    # prepare_campaign_messages sets the campaign to PREPARED and commits, and
    # the queue bridge only claims items whose campaign is already RUNNING.
    _auto_prepare(db, campaign.id)

    preflight_payload: dict[str, Any] | None = None
    if campaign.platform == PlatformType.RUBIKA:
        try:
            preflight = await evaluate_campaign_send_preflight(db, campaign.id)
        except Exception as exc:
            db.rollback()
            log_campaign_safety_event(
                "campaign_preflight_blocked",
                campaign_id=campaign.id,
                code=CAMPAIGN_DEPENDENCY_ERROR,
                extra={"error": type(exc).__name__, "request_id": request_id},
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code": CAMPAIGN_DEPENDENCY_ERROR,
                    "message": "خطای وابستگی سیستمی؛ وضعیت کمپین تغییر نکرد.",
                    "request_id": request_id,
                },
            ) from exc
        preflight_payload = preflight.to_dict()

        # GET /preflight keeps allowed_to_start=False while Controlled Production
        # still needs an explicit Confirm. After Confirm, that advisory blocker is
        # satisfied — enforce technical readiness only (implied by confirmation_required).
        cp_confirmation_satisfied = bool(
            confirm_controlled_production
            and getattr(preflight, "controlled_production_confirmation_required", False)
        )
        if cp_confirmation_satisfied:
            log_campaign_safety_event(
                "campaign_controlled_production_confirmed",
                campaign_id=campaign.id,
                code=CONTROLLED_PRODUCTION_APPROVAL_REQUIRED,
                extra={
                    "request_id": request_id,
                    "controlled_confirmation_received": True,
                    "technical_ready": bool(getattr(preflight, "technical_ready", False)),
                    "preflight_result": preflight.code,
                },
            )
        elif not preflight.allowed_to_start:
            status_code = 503 if preflight.code in {
                CAMPAIGN_CAPACITY_UNKNOWN,
                CAMPAIGN_DEPENDENCY_ERROR,
            } else 409
            log_campaign_safety_event(
                "campaign_preflight_blocked",
                campaign_id=campaign.id,
                code=preflight.code,
                extra={
                    "request_id": request_id,
                    "controlled_confirmation_received": bool(
                        confirm_controlled_production
                    ),
                },
            )
            raise HTTPException(
                status_code=status_code,
                detail={
                    "code": preflight.code,
                    "message": preflight.message,
                    "blockers": preflight.blockers,
                    "warnings": preflight.warnings,
                    "execution_safety_state": preflight.execution_safety_state,
                    "request_id": request_id,
                },
            )

    campaign.status = CampaignStatus.RUNNING.value
    db.flush()

    try:
        await clear_campaign_pause(campaign.id)
    except CampaignControlError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    if campaign.platform == PlatformType.RUBIKA:
        try:
            from core_engine.services.campaign_inflight import clear_campaign_safety_pause

            await clear_campaign_safety_pause(get_redis_client(), campaign.id)
        except Exception:
            pass

    bridge_result: dict[str, Any] | None = None
    if trigger_bridge:
        settings = get_settings()
        batch = int(getattr(settings, "CAMPAIGN_DISPATCH_BATCH_SIZE", 50) or 50)
        bridge_result = await push_staged_items_to_worker_queue(db, batch_size=batch)

    queue_jobs_created = 0
    if isinstance(bridge_result, dict):
        try:
            queue_jobs_created = int(bridge_result.get("pushed") or 0)
        except (TypeError, ValueError):
            queue_jobs_created = 0

    message = "Campaign is already running." if already_running else "Campaign started successfully."
    result: dict[str, Any] = {
        "status": "running",
        "campaign_id": campaign.id,
        "message": message,
        "bridge_result": bridge_result,
        "request_id": request_id,
        "accepted": True,
        "campaign_status": CampaignStatus.RUNNING.value,
        "queue_jobs_created": queue_jobs_created,
        "messages_scheduled": queue_jobs_created,
        "controlled_confirmation_accepted": bool(
            confirm_controlled_production
            and cp_enabled
            and campaign.platform == PlatformType.RUBIKA
        ),
    }
    log_campaign_safety_event(
        "campaign_start_accepted",
        campaign_id=campaign.id,
        code="CAMPAIGN_STARTED",
        extra={
            "request_id": request_id,
            "controlled_confirmation_received": bool(confirm_controlled_production),
            "controlled_confirmation_accepted": result["controlled_confirmation_accepted"],
            "queue_job_created": queue_jobs_created > 0,
            "queue_jobs_created": queue_jobs_created,
            "start_result": "accepted",
            "campaign_status": CampaignStatus.RUNNING.value,
        },
    )
    if preflight_payload is not None:
        result["preflight"] = {
            "code": preflight_payload.get("code"),
            "allowed_to_start": preflight_payload.get("allowed_to_start"),
            "warnings": preflight_payload.get("warnings") or [],
            "execution_safety_state": preflight_payload.get("execution_safety_state"),
            "estimated_completion_at": preflight_payload.get("estimated_completion_at"),
            "estimated_today_capacity": preflight_payload.get("estimated_today_capacity"),
            "technical_ready": preflight_payload.get("technical_ready"),
            "controlled_production_confirmation_required": preflight_payload.get(
                "controlled_production_confirmation_required"
            ),
        }
        if preflight_payload.get("warnings"):
            log_campaign_safety_event(
                "campaign_capacity_warning",
                campaign_id=campaign.id,
                code=str(preflight_payload.get("code")),
            )
    return result


async def stop_campaign(db: Session, campaign: Campaign) -> dict[str, Any]:
    if campaign.status not in _STOPPABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Campaign cannot be stopped from status '{campaign.status}'.",
        )

    already_paused = campaign.status == CampaignStatus.PAUSED.value
    campaign.status = CampaignStatus.PAUSED.value
    from core_engine.services.campaign_send_safety import (
        inflight_staged_count,
        invalidate_unsent_leases,
        stop_progress_label,
    )

    invalidate_unsent_leases(db, campaign.id)
    inflight = inflight_staged_count(db, campaign.id)
    db.flush()

    try:
        await set_campaign_pause(campaign.id)
    except CampaignControlError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    message = stop_progress_label(inflight)
    if already_paused and inflight == 0:
        message = stop_progress_label(0)
    return {
        "status": "paused",
        "campaign_id": campaign.id,
        "message": message,
        "paused_in_redis": True,
        "inflight": inflight,
        "fully_stopped": inflight == 0,
    }


def _resume_result(
    *,
    accepted: bool,
    code: str,
    campaign_id: int,
    campaign_status: str | None,
    emergency_stop_enabled: bool,
    message: str,
    status_code: int = 200,
    bridge_result: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "accepted": accepted,
        "code": code,
        "campaign_id": int(campaign_id),
        "campaign_status": campaign_status,
        "emergency_stop_enabled": emergency_stop_enabled,
        "message": message,
        "status_code": status_code,
        "bridge_result": bridge_result,
    }


async def resume_running_campaign_queue(
    db: Session,
    campaign_id: int,
    *,
    actor: str,
) -> dict[str, Any]:
    """Push the remaining pilot cohort of one running campaign.

    Does not call Start, does not reprepare, and does not dispatch other
    campaigns. Claim stays blocked while the emergency stop is on. When the
    gates pass, this turns the stop off and pushes; any failure turns it back on.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from core_engine.models import (
        CampaignPilotCohortMember,
        CampaignPilotState,
        StagedQueueItem,
        StagedQueueItemStatus,
    )
    from core_engine.services.audit_service import record_audit
    from core_engine.services.campaign_pilot_cohort import cohort_guard_active
    from core_engine.services.campaign_send_safety import rubika_window_open
    from core_engine.services.queue_bridge import emergency_stop_allows_claim
    from workers.redis_flags import set_system_kill_switch

    def _audit(code: str, extra: dict[str, Any] | None = None) -> None:
        record_audit(
            db,
            actor,
            "resume_campaign_queue",
            "campaign",
            str(int(campaign_id)),
            {"code": code, **(extra or {})},
        )

    try:
        db.execute(text("SET lock_timeout = '2s'"))
        campaign = (
            db.query(Campaign)
            .filter(Campaign.id == int(campaign_id))
            .with_for_update()
            .one_or_none()
        )
    except OperationalError:
        db.rollback()
        return _resume_result(
            accepted=False,
            code="LOCK_TIMEOUT",
            campaign_id=campaign_id,
            campaign_status=None,
            emergency_stop_enabled=True,
            message="قفل کمپین در مهلت تعیین‌شده آزاد نشد.",
            status_code=503,
        )

    if campaign is None:
        return _resume_result(
            accepted=False,
            code="CAMPAIGN_NOT_FOUND",
            campaign_id=campaign_id,
            campaign_status=None,
            emergency_stop_enabled=True,
            message="Campaign not found.",
            status_code=404,
        )
    if campaign.archived_at is not None:
        db.rollback()
        return _resume_result(
            accepted=False,
            code="CAMPAIGN_ARCHIVED",
            campaign_id=campaign.id,
            campaign_status=str(campaign.status),
            emergency_stop_enabled=True,
            message="Campaign is archived.",
            status_code=409,
        )
    if str(campaign.status) != CampaignStatus.RUNNING.value:
        db.rollback()
        return _resume_result(
            accepted=False,
            code="NOT_RUNNING",
            campaign_id=campaign.id,
            campaign_status=str(campaign.status),
            emergency_stop_enabled=True,
            message="Resume فقط برای کمپین running مجاز است.",
            status_code=409,
        )

    state = (
        db.query(CampaignPilotState)
        .filter(CampaignPilotState.campaign_id == campaign.id)
        .first()
    )
    cohort_size = (
        db.query(CampaignPilotCohortMember.id)
        .filter(CampaignPilotCohortMember.campaign_id == campaign.id)
        .count()
    )
    if not cohort_guard_active(state) or cohort_size < 1:
        db.rollback()
        return _resume_result(
            accepted=False,
            code="PILOT_COHORT_REQUIRED",
            campaign_id=campaign.id,
            campaign_status=str(campaign.status),
            emergency_stop_enabled=True,
            message="Pilot Cohort فعال لازم است.",
            status_code=409,
        )

    status_name = str(campaign.status)
    redis = get_redis_client()
    try:
        stop_allows_claim = await emergency_stop_allows_claim(redis)
    except Exception:
        db.rollback()
        return _resume_result(
            accepted=False,
            code="EMERGENCY_STOP_UNKNOWN",
            campaign_id=campaign.id,
            campaign_status=status_name,
            emergency_stop_enabled=True,
            message="وضعیت Emergency Stop خوانده نشد.",
            status_code=503,
        )

    if campaign.platform == PlatformType.RUBIKA:
        try:
            window_open, _next_at = rubika_window_open(db)
        except Exception:
            window_open = False
        if not window_open:
            db.rollback()
            _audit("WINDOW_CLOSED")
            db.commit()
            return _resume_result(
                accepted=False,
                code="WINDOW_CLOSED",
                campaign_id=campaign.id,
                campaign_status=status_name,
                emergency_stop_enabled=not stop_allows_claim,
                message="خارج از پنجره ارسال، Resume صف نمی‌سازد.",
                status_code=409,
            )
        try:
            from core_engine.services.rubika_circuit import get_circuit_snapshot
            from workers.config import get_worker_settings

            wsettings = get_worker_settings()
            snap = await get_circuit_snapshot(
                redis,
                probe_budget=int(wsettings.RUBIKA_CIRCUIT_PROBE_BUDGET),
                window_seconds=int(wsettings.RUBIKA_CIRCUIT_WINDOW_SECONDS),
            )
            if snap.state == "open":
                db.rollback()
                _audit("CIRCUIT_OPEN")
                db.commit()
                return _resume_result(
                    accepted=False,
                    code="CIRCUIT_OPEN",
                    campaign_id=campaign.id,
                    campaign_status=status_name,
                    emergency_stop_enabled=not stop_allows_claim,
                    message="Circuit باز است.",
                    status_code=409,
                )
        except Exception:
            db.rollback()
            return _resume_result(
                accepted=False,
                code="CIRCUIT_UNKNOWN",
                campaign_id=campaign.id,
                campaign_status=status_name,
                emergency_stop_enabled=True,
                message="وضعیت Circuit خوانده نشد.",
                status_code=503,
            )

    ready_cohort = (
        db.query(StagedQueueItem.id)
        .join(
            CampaignPilotCohortMember,
            (CampaignPilotCohortMember.campaign_id == StagedQueueItem.campaign_id)
            & (CampaignPilotCohortMember.contact_id == StagedQueueItem.contact_id),
        )
        .filter(
            StagedQueueItem.campaign_id == campaign.id,
            StagedQueueItem.status == StagedQueueItemStatus.READY.value,
        )
        .count()
    )
    active_cohort = (
        db.query(StagedQueueItem.id)
        .join(
            CampaignPilotCohortMember,
            (CampaignPilotCohortMember.campaign_id == StagedQueueItem.campaign_id)
            & (CampaignPilotCohortMember.contact_id == StagedQueueItem.contact_id),
        )
        .filter(
            StagedQueueItem.campaign_id == campaign.id,
            StagedQueueItem.status.in_(
                (
                    StagedQueueItemStatus.QUEUED.value,
                    StagedQueueItemStatus.PUSHING.value,
                )
            ),
        )
        .count()
    )
    campaign_pk = int(campaign.id)
    if ready_cohort < 1:
        db.rollback()
        code = "ALREADY_QUEUED" if active_cohort else "NOTHING_READY"
        _audit(code, {"active_cohort": int(active_cohort)})
        db.commit()
        return _resume_result(
            accepted=True,
            code=code,
            campaign_id=campaign_pk,
            campaign_status=status_name,
            emergency_stop_enabled=not stop_allows_claim,
            message="صف جدیدی ساخته نشد.",
            bridge_result={"pushed": 0},
        )

    # Release the campaign row before the bridge commits item locks.
    db.rollback()
    disabled_here = False
    try:
        if not stop_allows_claim:
            await set_system_kill_switch(redis, enabled=False)
            disabled_here = True
        bridge_result = await push_staged_items_to_worker_queue(
            db,
            batch_size=max(1, int(cohort_size)),
            campaign_id=campaign_pk,
            refill_staging=False,
            safety_pause_others=False,
        )
        pushed = int(bridge_result.get("pushed") or 0)
        if disabled_here and pushed < 1:
            await set_system_kill_switch(redis, enabled=True)
            disabled_here = False
            _audit("REFILL_EMPTY", {"bridge_result": bridge_result})
            db.commit()
            return _resume_result(
                accepted=False,
                code="REFILL_EMPTY",
                campaign_id=campaign_pk,
                campaign_status=status_name,
                emergency_stop_enabled=True,
                message="صف ساخته نشد و Emergency Stop دوباره روشن شد.",
                status_code=409,
                bridge_result=bridge_result,
            )
        stop_enabled = not await emergency_stop_allows_claim(redis)
        _audit("RESUMED", {"bridge_result": bridge_result, "disabled_here": disabled_here})
        db.commit()
        return _resume_result(
            accepted=True,
            code="RESUMED",
            campaign_id=campaign_pk,
            campaign_status=status_name,
            emergency_stop_enabled=stop_enabled,
            message="صف Cohort از مسیر رسمی ساخته شد.",
            bridge_result=bridge_result,
        )
    except Exception as exc:
        db.rollback()
        if disabled_here:
            try:
                await set_system_kill_switch(redis, enabled=True)
            except Exception:
                logger.exception("resume_restore_kill_switch_failed campaign_id=%s", campaign_id)
        logger.exception("resume_running_campaign_queue_failed campaign_id=%s", campaign_id)
        try:
            _audit("RESUME_FAILED", {"error": type(exc).__name__})
            db.commit()
        except Exception:
            db.rollback()
        return _resume_result(
            accepted=False,
            code="RESUME_FAILED",
            campaign_id=campaign_id,
            campaign_status=status_name,
            emergency_stop_enabled=True,
            message="Resume ناموفق بود و Emergency Stop روشن ماند.",
            status_code=503,
        )
