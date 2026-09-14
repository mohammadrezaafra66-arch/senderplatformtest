"""Generic soft-archive for Accounts and Campaigns.

Archive is orthogonal to technical/runtime status. Historical rows are never
destroyed. Workers must re-check archive state immediately before provider send.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    SendStatus,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.services.audit_service import record_audit

ERROR_ACCOUNT_ARCHIVED = "ACCOUNT_ARCHIVED"
ERROR_CAMPAIGN_ARCHIVED = "CAMPAIGN_ARCHIVED"

_CANCELABLE_STAGED = frozenset(
    {
        StagedQueueItemStatus.STAGED.value,
        StagedQueueItemStatus.READY.value,
        StagedQueueItemStatus.BLOCKED.value,
        StagedQueueItemStatus.QUEUED.value,
        # PUSHING is mid-claim — still not provider-delivered; cancel safely.
        StagedQueueItemStatus.PUSHING.value,
    }
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_account_archived(account: Account | None) -> bool:
    return account is not None and account.archived_at is not None


def is_campaign_archived(campaign: Campaign | None) -> bool:
    return campaign is not None and campaign.archived_at is not None


def require_account_not_archived(account: Account) -> None:
    if is_account_archived(account):
        raise HTTPException(
            status_code=409,
            detail={
                "code": ERROR_ACCOUNT_ARCHIVED,
                "message": "این اکانت آرشیو شده و برای ارسال قابل استفاده نیست.",
            },
        )


def require_campaign_not_archived(campaign: Campaign) -> None:
    if is_campaign_archived(campaign):
        raise HTTPException(
            status_code=409,
            detail={
                "code": ERROR_CAMPAIGN_ARCHIVED,
                "message": "این کمپین آرشیو شده و قابل اجرا نیست.",
            },
        )


@dataclass(slots=True)
class ArchiveResult:
    entity_type: str
    entity_id: int
    already_archived: bool
    archived_at: datetime | None
    previous_status: str | None = None
    queued_items_cancelled: int = 0
    pending_recipients_stopped: int = 0
    already_sent_count: int = 0
    in_flight_count: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.archived_at is not None:
            payload["archived_at"] = self.archived_at.isoformat()
        return payload


@dataclass(slots=True)
class RestoreResult:
    entity_type: str
    entity_id: int
    already_active: bool
    restored_at: datetime
    previous_status: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["restored_at"] = self.restored_at.isoformat()
        return payload


def _count_campaign_send_progress(db: Session, campaign_id: int) -> tuple[int, int, int]:
    """Return (already_sent, pending_recipients, in_flight)."""
    already_sent = (
        db.query(CampaignRecipient)
        .filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.send_status.in_(
                [
                    SendStatus.DELIVERED.value,
                    SendStatus.READ.value,
                    SendStatus.ACCEPTED_BY_PLATFORM.value,
                    SendStatus.DRY_RUN.value,
                    SendStatus.SHADOW_SENT.value,
                ]
            ),
        )
        .count()
    )
    in_flight = (
        db.query(CampaignRecipient)
        .filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.send_status.in_(
                [
                    SendStatus.PROCESSING.value,
                    SendStatus.QUEUED.value,
                    SendStatus.ACCEPTED_BY_WORKER.value,
                ]
            ),
        )
        .count()
    )
    pending = (
        db.query(CampaignRecipient)
        .filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.send_status.in_(
                [
                    SendStatus.PENDING.value,
                ]
            ),
        )
        .count()
    )
    return int(already_sent), int(pending), int(in_flight)


def _cancel_undispatched_staged_items(db: Session, campaign_id: int) -> int:
    items = (
        db.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == campaign_id,
            StagedQueueItem.status.in_(list(_CANCELABLE_STAGED)),
        )
        .all()
    )
    for item in items:
        item.status = StagedQueueItemStatus.SKIPPED.value
        item.skip_reason = ERROR_CAMPAIGN_ARCHIVED
    return len(items)


def _stop_pending_recipients(db: Session, campaign_id: int) -> int:
    rows = (
        db.query(CampaignRecipient)
        .filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.send_status == SendStatus.PENDING.value,
        )
        .all()
    )
    for row in rows:
        row.send_status = SendStatus.FAILED_PERMANENT.value
        row.failure_reason = ERROR_CAMPAIGN_ARCHIVED
    return len(rows)


def archive_account(
    db: Session,
    account: Account,
    *,
    actor: str | None = None,
    reason: str | None = None,
) -> ArchiveResult:
    previous = account.status.value if hasattr(account.status, "value") else str(account.status)
    if is_account_archived(account):
        return ArchiveResult(
            entity_type="account",
            entity_id=int(account.id),
            already_archived=True,
            archived_at=account.archived_at,
            previous_status=previous,
        )

    now = utcnow()
    account.archived_at = now
    account.archived_by = (actor or "")[:128] or None
    account.archive_reason = (reason or "")[:512] or None
    account.updated_at = datetime.utcnow()

    # Pending queue work for this account: cancel undispatched staged items whose
    # payload targets this account (campaign-scoped items still re-checked by worker).
    pending_jobs = 0
    staged = (
        db.query(StagedQueueItem)
        .filter(StagedQueueItem.status.in_(list(_CANCELABLE_STAGED)))
        .all()
    )
    for item in staged:
        payload = item.queue_payload or {}
        try:
            aid = int(payload.get("account_id"))
        except (TypeError, ValueError):
            continue
        if aid == int(account.id):
            item.status = StagedQueueItemStatus.SKIPPED.value
            item.skip_reason = ERROR_ACCOUNT_ARCHIVED
            pending_jobs += 1

    record_audit(
        db,
        actor or "system",
        "account_archived",
        "account",
        str(account.id),
        {
            "previous_status": previous,
            "queued_items_cancelled": pending_jobs,
            "reason": reason,
        },
    )
    db.flush()
    return ArchiveResult(
        entity_type="account",
        entity_id=int(account.id),
        already_archived=False,
        archived_at=now,
        previous_status=previous,
        queued_items_cancelled=pending_jobs,
    )


def restore_account(
    db: Session,
    account: Account,
    *,
    actor: str | None = None,
) -> RestoreResult:
    previous = account.status.value if hasattr(account.status, "value") else str(account.status)
    now = utcnow()
    if not is_account_archived(account):
        return RestoreResult(
            entity_type="account",
            entity_id=int(account.id),
            already_active=True,
            restored_at=now,
            previous_status=previous,
        )

    account.archived_at = None
    account.archived_by = None
    account.archive_reason = None
    account.updated_at = datetime.utcnow()
    # Do NOT mutate Account.status — runtime eligibility recomputed from truth.

    record_audit(
        db,
        actor or "system",
        "account_restored",
        "account",
        str(account.id),
        {"previous_status": previous},
    )
    db.flush()
    return RestoreResult(
        entity_type="account",
        entity_id=int(account.id),
        already_active=False,
        restored_at=now,
        previous_status=previous,
        details={"note": "runtime eligibility must be recomputed; no auto-send"},
    )


def archive_campaign(
    db: Session,
    campaign: Campaign,
    *,
    actor: str | None = None,
    reason: str | None = None,
) -> ArchiveResult:
    previous = campaign.status
    if is_campaign_archived(campaign):
        return ArchiveResult(
            entity_type="campaign",
            entity_id=int(campaign.id),
            already_archived=True,
            archived_at=campaign.archived_at,
            previous_status=previous,
        )

    already_sent, pending, in_flight = _count_campaign_send_progress(db, int(campaign.id))
    cancelled = _cancel_undispatched_staged_items(db, int(campaign.id))
    stopped = _stop_pending_recipients(db, int(campaign.id))

    now = utcnow()
    campaign.archived_at = now
    campaign.archived_by = (actor or "")[:128] or None
    campaign.archive_reason = (reason or "")[:512] or None
    # Stop future execution: if running, move to paused so queue bridge excludes it.
    if campaign.status == CampaignStatus.RUNNING.value:
        campaign.status = CampaignStatus.PAUSED.value
    campaign.updated_at = datetime.utcnow()

    record_audit(
        db,
        actor or "system",
        "campaign_archived",
        "campaign",
        str(campaign.id),
        {
            "previous_status": previous,
            "queued_items_cancelled": cancelled,
            "pending_recipients_stopped": stopped,
            "already_sent_count": already_sent,
            "in_flight_count": in_flight,
            "reason": reason,
        },
    )
    db.flush()
    return ArchiveResult(
        entity_type="campaign",
        entity_id=int(campaign.id),
        already_archived=False,
        archived_at=now,
        previous_status=previous,
        queued_items_cancelled=cancelled,
        pending_recipients_stopped=stopped,
        already_sent_count=already_sent,
        in_flight_count=in_flight,
    )


def restore_campaign(
    db: Session,
    campaign: Campaign,
    *,
    actor: str | None = None,
) -> RestoreResult:
    previous = campaign.status
    now = utcnow()
    if not is_campaign_archived(campaign):
        return RestoreResult(
            entity_type="campaign",
            entity_id=int(campaign.id),
            already_active=True,
            restored_at=now,
            previous_status=previous,
        )

    campaign.archived_at = None
    campaign.archived_by = None
    campaign.archive_reason = None
    # Never auto-start / auto-enqueue. Leave status as-is (often paused after archive).
    campaign.updated_at = datetime.utcnow()

    record_audit(
        db,
        actor or "system",
        "campaign_restored",
        "campaign",
        str(campaign.id),
        {
            "previous_status": previous,
            "auto_start": False,
            "auto_send": False,
        },
    )
    db.flush()
    return RestoreResult(
        entity_type="campaign",
        entity_id=int(campaign.id),
        already_active=False,
        restored_at=now,
        previous_status=previous,
        details={"note": "explicit Start required; no auto-send"},
    )


def account_archive_blocks_send(db: Session, account_id: int) -> bool:
    row = db.query(Account.archived_at).filter(Account.id == int(account_id)).first()
    return row is not None and row[0] is not None


def campaign_archive_blocks_send(db: Session, campaign_id: int) -> bool:
    row = db.query(Campaign.archived_at).filter(Campaign.id == int(campaign_id)).first()
    return row is not None and row[0] is not None
