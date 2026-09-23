"""Stop, send-window, success ledger, pilot permits, and shared product snapshot.

Account hourly and daily limits are not decided here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core_engine.models import (
    Campaign,
    CampaignPilotState,
    CampaignSendSuccess,
    CampaignStatus,
    ProductSendSnapshot,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.services.campaign_capacity import (
    SendWindowSpec,
    next_window_start,
    policy_now,
)
from core_engine.services.campaign_preflight import load_send_windows
from core_engine.services.product_feed.errors import ProductFeedError
from core_engine.services.product_feed.send_time_refresh import (
    PRODUCT_REFRESH_FAILED,
    PRODUCT_SNAPSHOT_MAX_AGE_SECONDS,
)

STOP_STOPPING = "در حال توقف؛ منتظر پایان ارسال‌های جاری"
STOP_STOPPED = "کاملاً متوقف شد"
PILOT_SUCCESS_LIMIT = 100

_INFLIGHT_STAGED = (
    StagedQueueItemStatus.PUSHING.value,
    StagedQueueItemStatus.QUEUED.value,
)


def logical_send_key(campaign_id: int, contact_id: int) -> str:
    return f"campaign:{int(campaign_id)}:contact:{int(contact_id)}"


def stop_progress_label(inflight: int) -> str:
    return STOP_STOPPING if int(inflight) > 0 else STOP_STOPPED


def inflight_staged_count(db: Session, campaign_id: int) -> int:
    return int(
        db.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == campaign_id,
            StagedQueueItem.status.in_(_INFLIGHT_STAGED),
        )
        .count()
    )


def invalidate_unsent_leases(db: Session, campaign_id: int) -> int:
    """Return queued rows that have not reached the connector to ready."""
    released = 0
    items = (
        db.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == campaign_id,
            StagedQueueItem.status == StagedQueueItemStatus.QUEUED.value,
        )
        .all()
    )
    for item in items:
        payload = item.queue_payload if isinstance(item.queue_payload, dict) else {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        if metadata.get("external_send_submitted") is True:
            continue
        item.status = StagedQueueItemStatus.READY.value
        released += 1
    db.flush()
    return released


def pre_connector_decision(
    *,
    kill_switch: bool,
    campaign_paused: bool,
    window_open: bool,
    already_submitted: bool,
) -> str:
    """Return allow, stop, or defer. A request already on the wire is not recalled."""
    if already_submitted:
        return "inflight_submitted"
    if kill_switch or campaign_paused:
        return "stop"
    if not window_open:
        return "defer"
    return "allow"


def window_is_open(
    windows: list[SendWindowSpec],
    now: datetime | None = None,
) -> tuple[bool, datetime | None]:
    """Read the decision for this instant. Empty schedule does not invent a 5-cap."""
    local = policy_now(clock=now)
    if not windows:
        return True, None
    from core_engine.services.campaign_capacity import current_window_phase

    if current_window_phase(windows, local) is not None:
        return True, None
    return False, next_window_start(windows, local)


def rubika_window_open(db: Session, now: datetime | None = None) -> tuple[bool, datetime | None]:
    """Load the frontend schedule from the database on every check."""
    return window_is_open(load_send_windows(db), now)


def record_definitive_success(
    db: Session,
    *,
    campaign_id: int,
    contact_id: int,
    message_id: int | None,
) -> bool:
    """Insert one ledger row. False means this logical send was already counted."""
    key = logical_send_key(campaign_id, contact_id)
    statement = (
        pg_insert(CampaignSendSuccess)
        .values(
            campaign_id=int(campaign_id),
            contact_id=int(contact_id),
            message_id=message_id,
            idempotency_key=key,
        )
        .on_conflict_do_nothing(constraint="uq_campaign_send_success_recipient")
    )
    result = db.execute(statement)
    db.flush()
    return int(result.rowcount or 0) == 1


def success_count(db: Session, campaign_id: int) -> int:
    return int(
        db.query(CampaignSendSuccess)
        .filter(CampaignSendSuccess.campaign_id == campaign_id)
        .count()
    )


def ensure_pilot(db: Session, campaign_id: int, *, enabled: bool = True) -> CampaignPilotState:
    row = db.get(CampaignPilotState, campaign_id)
    if row is None:
        row = CampaignPilotState(
            campaign_id=campaign_id,
            enabled=enabled,
            success_limit=PILOT_SUCCESS_LIMIT,
            auto_pause=True,
        )
        db.add(row)
        db.flush()
    return row


def reserve_pilot_permit(db: Session, campaign_id: int) -> bool:
    row = (
        db.query(CampaignPilotState)
        .filter(CampaignPilotState.campaign_id == campaign_id)
        .with_for_update()
        .first()
    )
    if row is None or not row.enabled:
        return True
    if row.confirmed + row.reserved >= int(row.success_limit):
        return False
    row.reserved = int(row.reserved) + 1
    db.flush()
    return True


def release_pilot_permit(db: Session, campaign_id: int) -> None:
    row = (
        db.query(CampaignPilotState)
        .filter(CampaignPilotState.campaign_id == campaign_id)
        .with_for_update()
        .first()
    )
    if row is None or not row.enabled:
        return
    row.reserved = max(0, int(row.reserved) - 1)
    db.flush()


def confirm_pilot_success(db: Session, campaign_id: int) -> bool:
    """Count one success. Pause exactly when the confirmed total reaches the limit."""
    row = (
        db.query(CampaignPilotState)
        .filter(CampaignPilotState.campaign_id == campaign_id)
        .with_for_update()
        .first()
    )
    if row is None or not row.enabled:
        return False
    row.reserved = max(0, int(row.reserved) - 1)
    row.confirmed = int(row.confirmed) + 1
    paused = False
    if row.auto_pause and row.confirmed >= int(row.success_limit):
        campaign = db.get(Campaign, campaign_id)
        if campaign is not None and campaign.status != CampaignStatus.PAUSED.value:
            campaign.status = CampaignStatus.PAUSED.value
            paused = True
    db.flush()
    return paused


def pilot_blocks_refill(db: Session, campaign_id: int) -> bool:
    row = db.get(CampaignPilotState, campaign_id)
    if row is None or not row.enabled:
        return False
    return int(row.confirmed) >= int(row.success_limit)


def pilot_blocks_start(db: Session, campaign_id: int) -> bool:
    row = db.get(CampaignPilotState, campaign_id)
    if row is None or not row.enabled:
        return False
    if int(row.confirmed) < int(row.success_limit):
        return False
    return not bool(row.admin_resume_confirmed)


def confirm_pilot_resume(db: Session, campaign_id: int) -> None:
    row = db.get(CampaignPilotState, campaign_id)
    if row is None:
        return
    row.admin_resume_confirmed = True
    db.flush()


def pause_all_running(db: Session) -> int:
    rows = (
        db.query(Campaign)
        .filter(Campaign.status == CampaignStatus.RUNNING.value)
        .all()
    )
    for campaign in rows:
        campaign.status = CampaignStatus.PAUSED.value
    db.flush()
    return len(rows)


def load_or_refresh_shared_snapshot(
    db: Session,
    fetcher: Callable[[], list],
    *,
    now: datetime | None = None,
    max_age_seconds: int = PRODUCT_SNAPSHOT_MAX_AGE_SECONDS,
) -> tuple[list, bool]:
    """One locked refresh. Other workers wait and then read the same payload."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    row = (
        db.query(ProductSendSnapshot)
        .filter(ProductSendSnapshot.id == 1)
        .with_for_update()
        .first()
    )
    if row is None:
        row = ProductSendSnapshot(id=1, payload=[], fetched_at=None)
        db.add(row)
        db.flush()
    fresh = False
    if row.fetched_at is not None and row.payload:
        age = (current - row.fetched_at).total_seconds()
        fresh = age <= int(max_age_seconds)
    if fresh:
        return list(row.payload or []), False
    try:
        payload = list(fetcher() or [])
    except ProductFeedError:
        raise
    except Exception as exc:
        raise ProductFeedError(PRODUCT_REFRESH_FAILED, "تازه‌سازی افراکالا ناموفق بود.") from exc
    row.payload = payload
    row.fetched_at = current
    db.flush()
    return payload, True
