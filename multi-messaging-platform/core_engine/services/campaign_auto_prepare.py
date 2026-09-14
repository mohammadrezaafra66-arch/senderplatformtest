"""Automatic campaign preparation when prerequisites are satisfied."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from core_engine.models import (
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    RenderStatus,
    RenderedMessage,
)
from core_engine.schemas.phase4 import PrepareMessagesRequest, PrepareMessagesResultResponse
from core_engine.services.campaign_preparation import evaluate_preparation_readiness
from core_engine.services.phase4_prepare import prepare_campaign_messages

logger = logging.getLogger(__name__)

_AUTO_PREPARE_STATUSES = frozenset(
    {
        CampaignStatus.DRAFT.value,
        CampaignStatus.PREPARED.value,
    }
)
_PROTECTED_STATUSES = frozenset(
    {
        CampaignStatus.RUNNING.value,
        CampaignStatus.COMPLETED.value,
        CampaignStatus.FAILED.value,
    }
)
_SOFT_SKIP_CODES = frozenset(
    {
        "no_contacts",
        "NO_CONTACTS",
        "empty_campaign",
        "EMPTY_CAMPAIGN",
        "template_text_empty",
        "TEMPLATE_TEXT_EMPTY",
    }
)


@dataclass
class AutoPrepareResult:
    attempted: bool = False
    prepared: bool = False
    skipped: bool = False
    skip_reason: str | None = None
    blockers: list[dict[str, Any]] = field(default_factory=list)
    prepare_result: PrepareMessagesResultResponse | None = None
    error_code: str | None = None
    error_message: str | None = None
    trigger: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "prepared": self.prepared,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "blockers": self.blockers,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "trigger": self.trigger,
            "ready_count": (
                self.prepare_result.ready_count if self.prepare_result else None
            ),
            "staged_count": (
                self.prepare_result.staged_count if self.prepare_result else None
            ),
        }


def _prepared_message_count(db: Session, campaign_id: int) -> int:
    return int(
        db.query(RenderedMessage)
        .filter(
            RenderedMessage.campaign_id == campaign_id,
            RenderedMessage.ready_for_queue.is_(True),
        )
        .count()
        or 0
    )


def is_preparation_dirty(db: Session, campaign: Campaign) -> bool:
    """True when staged output no longer matches current campaign configuration."""
    prepared = _prepared_message_count(db, campaign.id)
    if prepared <= 0:
        return True

    pending = (
        db.query(CampaignRecipient)
        .filter(
            CampaignRecipient.campaign_id == campaign.id,
            CampaignRecipient.render_status == RenderStatus.PENDING,
        )
        .count()
    )
    if pending > 0:
        return True

    return campaign.status == CampaignStatus.DRAFT.value


def campaign_is_auto_prepare_candidate(campaign: Campaign) -> bool:
    return campaign.status in _AUTO_PREPARE_STATUSES


def try_auto_prepare_campaign(
    db: Session,
    campaign_id: int,
    *,
    trigger: str,
    force: bool = False,
) -> AutoPrepareResult:
    """Prepare campaign when prerequisites are complete. Idempotent and concurrency-safe."""
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(campaign_id)})

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if campaign is None:
        return AutoPrepareResult(skipped=True, skip_reason="not_found", trigger=trigger)

    if getattr(campaign, "archived_at", None) is not None:
        return AutoPrepareResult(
            skipped=True,
            skip_reason="campaign_archived",
            trigger=trigger,
        )

    if campaign.status in _PROTECTED_STATUSES:
        return AutoPrepareResult(
            skipped=True,
            skip_reason=f"protected_status:{campaign.status}",
            trigger=trigger,
        )

    if campaign.status not in _AUTO_PREPARE_STATUSES:
        return AutoPrepareResult(
            skipped=True,
            skip_reason=f"status_not_prepareable:{campaign.status}",
            trigger=trigger,
        )

    blockers = evaluate_preparation_readiness(db, campaign)
    if blockers:
        logger.info(
            "event=auto_prepare_blocked campaign_id=%s trigger=%s blockers=%s",
            campaign_id,
            trigger,
            [b.get("code") for b in blockers],
        )
        return AutoPrepareResult(blockers=blockers, trigger=trigger)

    if (
        not force
        and campaign.status == CampaignStatus.PREPARED.value
        and not is_preparation_dirty(db, campaign)
    ):
        return AutoPrepareResult(
            skipped=True,
            skip_reason="already_prepared",
            prepared=True,
            trigger=trigger,
        )

    result = AutoPrepareResult(attempted=True, trigger=trigger)
    try:
        prepare_result = prepare_campaign_messages(
            db,
            campaign_id,
            PrepareMessagesRequest(force_mock_output=False),
        )
        result.prepare_result = prepare_result
        result.prepared = prepare_result.ready_count > 0 or _prepared_message_count(
            db, campaign_id
        ) > 0
        logger.info(
            "event=auto_prepare_success campaign_id=%s trigger=%s ready=%s staged=%s",
            campaign_id,
            trigger,
            prepare_result.ready_count,
            prepare_result.staged_count,
        )
        return result
    except HTTPException as exc:
        db.rollback()
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = str(detail.get("code") or "") if isinstance(detail, dict) else None
        message = detail.get("message") if isinstance(detail, dict) else str(exc.detail)
        if code and code in _SOFT_SKIP_CODES:
            result.skipped = True
            result.skip_reason = code
            logger.info(
                "event=auto_prepare_soft_skip campaign_id=%s trigger=%s code=%s",
                campaign_id,
                trigger,
                code,
            )
            return result
        result.error_code = code or "PREPARE_FAILED"
        result.error_message = str(message) if message else "Prepare failed."
        logger.warning(
            "event=auto_prepare_failed campaign_id=%s trigger=%s code=%s",
            campaign_id,
            trigger,
            result.error_code,
        )
        return result


def auto_prepare_after_mutation(
    db: Session,
    campaign_id: int,
    *,
    trigger: str,
) -> AutoPrepareResult:
    """Hook for create/update paths — never raises."""
    try:
        return try_auto_prepare_campaign(db, campaign_id, trigger=trigger)
    except Exception:
        db.rollback()
        logger.exception(
            "event=auto_prepare_unexpected_error campaign_id=%s trigger=%s",
            campaign_id,
            trigger,
        )
        return AutoPrepareResult(
            attempted=True,
            error_code="PREPARE_UNEXPECTED_ERROR",
            error_message="Unexpected error during automatic preparation.",
            trigger=trigger,
        )
