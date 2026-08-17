"""Phase 6 controlled campaign dispatch helpers.

Fair per-campaign claiming, account skip, circuit halt, and in-flight
backpressure. Never reassigns WorkerPayload.account_id.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from core_engine.models import (
    Campaign,
    CampaignStatus,
    Message,
    PlatformType,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.services.campaign_capacity import SendWindowSpec
from core_engine.services.campaign_inflight import (
    get_skipped_account_ids,
    set_account_dispatch_skip,
    set_campaign_safety_pause,
)
from core_engine.services.campaign_preflight import (
    CAMPAIGN_CIRCUIT_OPEN,
    load_send_windows,
    log_campaign_safety_event,
)
from core_engine.services.campaign_retry_schedule import retry_hint_for_code
from core_engine.services.rubika_policy import policy_now

logger = logging.getLogger("core_engine.services.campaign_dispatch")

RUBIKA_CHANNELS = frozenset({"rubika", PlatformType.RUBIKA.value})


def dispatch_settings(settings: Any) -> dict[str, int]:
    return {
        "batch_size": int(getattr(settings, "CAMPAIGN_DISPATCH_BATCH_SIZE", 50) or 50),
        "max_in_flight": int(getattr(settings, "CAMPAIGN_DISPATCH_MAX_IN_FLIGHT", 200) or 200),
        "per_campaign": int(getattr(settings, "CAMPAIGN_DISPATCH_PER_CAMPAIGN_BATCH", 25) or 25),
        "interval_ms": int(getattr(settings, "CAMPAIGN_DISPATCH_INTERVAL_MS", 0) or 0),
        "per_account": int(getattr(settings, "RUBIKA_MAX_IN_FLIGHT_PER_ACCOUNT", 8) or 8),
        "inflight_ttl": int(getattr(settings, "RUBIKA_INFLIGHT_TTL_SECONDS", 180) or 180),
    }


def count_in_flight(db: Session, campaign_ids: Sequence[int]) -> int:
    if not campaign_ids:
        return 0
    return int(
        db.query(func.count(StagedQueueItem.id))
        .filter(
            StagedQueueItem.campaign_id.in_(list(campaign_ids)),
            StagedQueueItem.status.in_(
                (StagedQueueItemStatus.PUSHING.value, StagedQueueItemStatus.QUEUED.value)
            ),
        )
        .scalar()
        or 0
    )


def running_campaign_ids_with_ready_items(db: Session) -> list[int]:
    rows = (
        db.query(StagedQueueItem.campaign_id)
        .join(Campaign, StagedQueueItem.campaign_id == Campaign.id)
        .filter(
            StagedQueueItem.status == StagedQueueItemStatus.READY.value,
            Campaign.status == CampaignStatus.RUNNING.value,
            StagedQueueItem.rendered_message_id.isnot(None),
        )
        .distinct()
        .order_by(StagedQueueItem.campaign_id.asc())
        .all()
    )
    return [int(row[0]) for row in rows]


def rotate_campaign_ids(campaign_ids: list[int], cursor: int) -> list[int]:
    if not campaign_ids:
        return []
    idx = int(cursor) % len(campaign_ids)
    return campaign_ids[idx:] + campaign_ids[:idx]


def _ready_account_ids_for_campaigns(db: Session, campaign_ids: Sequence[int]) -> dict[int, set[int]]:
    if not campaign_ids:
        return {}
    rows = (
        db.query(StagedQueueItem.campaign_id, Message.account_id)
        .join(
            Message,
            (Message.campaign_id == StagedQueueItem.campaign_id)
            & (Message.contact_id == StagedQueueItem.contact_id),
        )
        .join(Campaign, Campaign.id == StagedQueueItem.campaign_id)
        .filter(
            StagedQueueItem.campaign_id.in_(list(campaign_ids)),
            StagedQueueItem.status == StagedQueueItemStatus.READY.value,
            Campaign.status == CampaignStatus.RUNNING.value,
            StagedQueueItem.rendered_message_id.isnot(None),
            Campaign.platform == PlatformType.RUBIKA,
        )
        .distinct()
        .all()
    )
    out: dict[int, set[int]] = {}
    for campaign_id, account_id in rows:
        if account_id is None:
            continue
        out.setdefault(int(campaign_id), set()).add(int(account_id))
    return out


async def blocked_rubika_account_ids(
    db: Session,
    redis: Any,
    campaign_ids: Sequence[int],
    *,
    circuit_open: bool,
    now: datetime | None = None,
    windows: Sequence[SendWindowSpec] | None = None,
) -> set[int]:
    """Accounts that must not receive new dispatch (no silent failover)."""
    blocked: set[int] = set()
    if circuit_open:
        mapping = _ready_account_ids_for_campaigns(db, campaign_ids)
        for accounts in mapping.values():
            blocked.update(accounts)
        return blocked

    mapping = _ready_account_ids_for_campaigns(db, campaign_ids)
    all_ids = sorted({aid for accounts in mapping.values() for aid in accounts})
    if not all_ids:
        return blocked

    skipped = await get_skipped_account_ids(redis, all_ids)
    blocked.update(skipped.keys())

    from core_engine.services.rubika_health import is_account_quarantined
    from core_engine.models import Account

    accounts = {
        int(row.id): row
        for row in db.query(Account).filter(Account.id.in_(all_ids)).all()
    }
    local = policy_now(clock=now)
    specs = list(windows) if windows is not None else load_send_windows(db)

    for account_id in all_ids:
        if account_id in blocked:
            continue
        account = accounts.get(account_id)
        if account is None:
            blocked.add(account_id)
            continue
        try:
            if await is_account_quarantined(redis, account_id):
                blocked.add(account_id)
                hint = retry_hint_for_code("ACCOUNT_QUARANTINED", now=local, windows=specs)
                await set_account_dispatch_skip(
                    redis, account_id, code="ACCOUNT_QUARANTINED", ttl_seconds=hint.delay_seconds
                )
                continue
        except Exception:  # noqa: BLE001 — fail closed for this account
            blocked.add(account_id)
            await set_account_dispatch_skip(
                redis, account_id, code="REDIS_UNAVAILABLE", ttl_seconds=30
            )
    return blocked


async def maybe_safety_pause_running_campaigns(
    db: Session,
    redis: Any,
    *,
    circuit_open: bool,
) -> None:
    if not circuit_open:
        return
    rows = (
        db.query(Campaign.id)
        .filter(
            Campaign.status == CampaignStatus.RUNNING.value,
            Campaign.platform == PlatformType.RUBIKA,
        )
        .all()
    )
    for (campaign_id,) in rows:
        await set_campaign_safety_pause(
            redis,
            int(campaign_id),
            code=CAMPAIGN_CIRCUIT_OPEN,
            reason="global_circuit_open",
        )
        log_campaign_safety_event(
            "campaign_safety_paused",
            campaign_id=int(campaign_id),
            code=CAMPAIGN_CIRCUIT_OPEN,
        )


def claim_ready_items(
    db: Session,
    *,
    campaign_ids: Sequence[int],
    batch_size: int,
    per_campaign: int,
    blocked_account_ids: set[int],
) -> list[StagedQueueItem]:
    claimed: list[StagedQueueItem] = []
    remaining = max(0, int(batch_size))
    per = max(1, int(per_campaign))
    for campaign_id in campaign_ids:
        if remaining <= 0:
            break
        take = min(per, remaining)
        query = (
            db.query(StagedQueueItem)
            .join(Campaign, StagedQueueItem.campaign_id == Campaign.id)
            .filter(
                StagedQueueItem.campaign_id == int(campaign_id),
                StagedQueueItem.status == StagedQueueItemStatus.READY.value,
                Campaign.status == CampaignStatus.RUNNING.value,
                StagedQueueItem.rendered_message_id.isnot(None),
            )
        )
        if blocked_account_ids:
            blocked_item_ids = (
                db.query(StagedQueueItem.id)
                .join(
                    Message,
                    (Message.campaign_id == StagedQueueItem.campaign_id)
                    & (Message.contact_id == StagedQueueItem.contact_id),
                )
                .filter(
                    StagedQueueItem.campaign_id == int(campaign_id),
                    Message.account_id.in_(list(blocked_account_ids)),
                )
            )
            query = query.filter(~StagedQueueItem.id.in_(blocked_item_ids))
        rows = (
            query.order_by(StagedQueueItem.id.asc())
            .limit(take)
            .with_for_update(skip_locked=True)
            .all()
        )
        claimed.extend(rows)
        remaining -= len(rows)
    return claimed


def payload_is_rubika(item: StagedQueueItem) -> bool:
    channel = str(item.channel or "").strip().lower()
    if channel in RUBIKA_CHANNELS:
        return True
    payload = item.queue_payload if isinstance(item.queue_payload, dict) else {}
    return str(payload.get("platform") or "").strip().lower() == "rubika"
