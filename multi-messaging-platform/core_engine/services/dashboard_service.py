"""سرویس‌های read-only داشبورد — آمار DB و وضعیت صف."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    Message,
    MessageAttempt,
    MessageAttemptStatus,
    SendStatus,
    StagedQueueItem,
)

SENT_SEND_STATUSES = frozenset(
    {
        SendStatus.DELIVERED,
        SendStatus.READ,
        SendStatus.ACCEPTED_BY_PLATFORM,
    }
)
FAILED_SEND_STATUSES = frozenset(
    {
        SendStatus.FAILED_RETRYABLE,
        SendStatus.FAILED_PERMANENT,
    }
)
QUEUED_SEND_STATUSES = frozenset(
    {
        SendStatus.PENDING,
        SendStatus.QUEUED,
    }
)
PROCESSING_SEND_STATUSES = frozenset(
    {
        SendStatus.PROCESSING,
        SendStatus.ACCEPTED_BY_WORKER,
    }
)


def get_dashboard_summary(db: Session) -> dict[str, int]:
    campaigns_total = db.query(Campaign).count()
    campaigns_running = (
        db.query(Campaign)
        .filter(Campaign.status == CampaignStatus.RUNNING.value)
        .count()
    )
    campaigns_paused = (
        db.query(Campaign)
        .filter(Campaign.status == CampaignStatus.PAUSED.value)
        .count()
    )

    messages_total = db.query(Message).count()
    messages_sent = (
        db.query(MessageAttempt)
        .filter(MessageAttempt.status == MessageAttemptStatus.SUCCESS)
        .count()
    )
    messages_failed = (
        db.query(MessageAttempt)
        .filter(
            MessageAttempt.status.in_(
                [
                    MessageAttemptStatus.FAILED_RETRYABLE,
                    MessageAttemptStatus.FAILED_PERMANENT,
                ]
            )
        )
        .count()
    )

    accounts_total = db.query(Account).count()
    accounts_active = (
        db.query(Account)
        .filter(Account.status == AccountStatus.ACTIVE)
        .count()
    )
    accounts_banned = (
        db.query(Account)
        .filter(Account.status == AccountStatus.BANNED)
        .count()
    )

    return {
        "campaigns_total": campaigns_total,
        "campaigns_running": campaigns_running,
        "campaigns_paused": campaigns_paused,
        "messages_total": messages_total,
        "messages_sent": messages_sent,
        "messages_failed": messages_failed,
        "accounts_total": accounts_total,
        "accounts_active": accounts_active,
        "accounts_banned": accounts_banned,
    }


def _stats_from_recipients(recipients: list[CampaignRecipient]) -> dict[str, int]:
    total = len(recipients)
    queued = processing = sent = failed = 0
    for recipient in recipients:
        status = recipient.send_status
        if status in QUEUED_SEND_STATUSES:
            queued += 1
        elif status in PROCESSING_SEND_STATUSES:
            processing += 1
        elif status in SENT_SEND_STATUSES:
            sent += 1
        elif status in FAILED_SEND_STATUSES:
            failed += 1
    return {
        "total_recipients": total,
        "queued": queued,
        "processing": processing,
        "sent": sent,
        "failed": failed,
    }


def _stats_from_contacts_and_staged(
    db: Session,
    campaign_id: int,
) -> dict[str, int]:
    total_recipients = (
        db.query(Contact)
        .filter(Contact.campaign_id == campaign_id)
        .count()
    )
    staged_items = (
        db.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign_id)
        .all()
    )
    if total_recipients == 0 and staged_items:
        total_recipients = len({item.contact_id for item in staged_items})

    queued = sum(1 for item in staged_items if item.status in {"ready", "staged"})
    processing = 0
    sent = 0
    failed = sum(1 for item in staged_items if item.status in {"blocked", "skipped"})

    return {
        "total_recipients": total_recipients,
        "queued": queued,
        "processing": processing,
        "sent": sent,
        "failed": failed,
    }


def get_campaign_stats(db: Session, campaign_id: int) -> dict[str, int | float | None]:
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    recipients = (
        db.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == campaign_id)
        .all()
    )
    if recipients:
        counts = _stats_from_recipients(recipients)
    else:
        counts = _stats_from_contacts_and_staged(db, campaign_id)

    total = counts["total_recipients"]
    completed = counts["sent"] + counts["failed"]
    progress_percent = round((completed / total) * 100, 2) if total > 0 else 0

    return {
        "campaign_id": campaign_id,
        "total_recipients": counts["total_recipients"],
        "queued": counts["queued"],
        "processing": counts["processing"],
        "sent": counts["sent"],
        "failed": counts["failed"],
        "progress_percent": progress_percent,
        "eta_seconds": None,
    }


def get_workers_status() -> dict[str, list[dict[str, str | None]]]:
    return {
        "workers": [
            {
                "name": "celery_worker",
                "status": "unknown",
                "last_seen_at": None,
            }
        ]
    }


EMPTY_DASHBOARD_SUMMARY: dict[str, int] = {
    "campaigns_total": 0,
    "campaigns_running": 0,
    "campaigns_paused": 0,
    "messages_total": 0,
    "messages_sent": 0,
    "messages_failed": 0,
    "accounts_total": 0,
    "accounts_active": 0,
    "accounts_banned": 0,
}


def _empty_dashboard_queues() -> list[dict[str, int | str]]:
    from core_engine.services.redis_client import DASHBOARD_QUEUE_NAMES

    return [{"name": name, "pending": 0} for name in DASHBOARD_QUEUE_NAMES]


async def build_dashboard_snapshot() -> dict[str, object]:
    """Build a dashboard snapshot payload for REST/WebSocket consumers."""
    from datetime import datetime, timezone

    from core_engine.database import SessionLocal
    from core_engine.services.redis_client import get_dashboard_queue_pending

    warnings: list[str] = []
    summary = dict(EMPTY_DASHBOARD_SUMMARY)

    db = SessionLocal()
    try:
        summary = get_dashboard_summary(db)
    except Exception:
        warnings.append("database_unavailable")
    finally:
        db.close()

    queues = _empty_dashboard_queues()
    try:
        queues, redis_connected = await get_dashboard_queue_pending()
        if not redis_connected:
            warnings.append("redis_unavailable")
    except Exception:
        warnings.append("redis_unavailable")

    workers = get_workers_status()["workers"]

    controls: dict[str, object] = {"kill_switch_enabled": False}
    try:
        from core_engine.services.control_service import get_kill_switch_enabled_for_snapshot

        controls["kill_switch_enabled"] = await get_kill_switch_enabled_for_snapshot()
    except Exception:
        warnings.append("controls_unavailable")

    return {
        "type": "dashboard_snapshot",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "queues": queues,
        "workers": workers,
        "warnings": warnings,
        "controls": controls,
    }

