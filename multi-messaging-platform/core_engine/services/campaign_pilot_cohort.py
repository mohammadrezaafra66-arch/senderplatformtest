"""Frozen pilot cohort. Membership is stored, not recomputed at claim time."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from core_engine.models import (
    Campaign,
    CampaignPilotCohortMember,
    CampaignPilotState,
    CampaignRecipient,
    CampaignSendSuccess,
    CampaignStatus,
    Message,
    MessageAttempt,
    SendStatus,
    StagedQueueItem,
    StagedQueueItemStatus,
)

# Same terminal delivery outcomes the worker already uses for campaign completion.
# failed_retryable and unknown stay open so an existing retry can run again.
COHORT_TERMINAL_SEND_STATUSES = (
    SendStatus.DELIVERED,
    SendStatus.READ,
    SendStatus.FAILED_PERMANENT,
    SendStatus.DRY_RUN,
    SendStatus.SHADOW_SENT,
    SendStatus.OPTED_OUT,
    SendStatus.BLACKLISTED,
)


class PilotCohortRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def cohort_guard_active(state: CampaignPilotState | None) -> bool:
    return (
        state is not None
        and bool(state.enabled)
        and state.recipient_limit is not None
    )


def idempotency_key_is_valid(campaign_id: object, contact_id: object) -> bool:
    from core_engine.services.campaign_send_safety import logical_send_key

    try:
        campaign = int(campaign_id)  # type: ignore[arg-type]
        contact = int(contact_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if campaign <= 0 or contact <= 0:
        return False
    key = logical_send_key(campaign, contact)
    return key == f"campaign:{campaign}:contact:{contact}" and len(key) <= 255


def configure_pilot_recipient_limit(
    db: Session,
    campaign_id: int,
    recipient_limit: int,
) -> CampaignPilotState:
    """Enable a recipient cap. An existing cohort stays frozen."""
    from core_engine.services.campaign_send_safety import ensure_pilot

    if int(recipient_limit) < 1:
        raise PilotCohortRejected("PILOT_LIMIT_INVALID", "حد مخاطب Pilot باید مثبت باشد.")
    campaign = (
        db.query(Campaign)
        .filter(Campaign.id == int(campaign_id))
        .with_for_update()
        .first()
    )
    if campaign is None:
        raise PilotCohortRejected("CAMPAIGN_NOT_FOUND", "Campaign not found.")
    state = ensure_pilot(db, int(campaign_id), enabled=True)
    stored = (
        db.query(CampaignPilotCohortMember.id)
        .filter(CampaignPilotCohortMember.campaign_id == int(campaign_id))
        .first()
    )
    if stored is not None and state.recipient_limit not in (None, int(recipient_limit)):
        raise PilotCohortRejected(
            "PILOT_COHORT_FROZEN",
            "اعضای Pilot ثبت شده‌اند و با تغییر سقف عوض نمی‌شوند.",
        )
    state.enabled = True
    state.auto_pause = True
    if state.recipient_limit is None:
        state.recipient_limit = int(recipient_limit)
    db.flush()
    return state


def materialize_pilot_cohort(db: Session, campaign_id: int) -> dict:
    """Insert the first N recipients once. A second call returns the same rows."""
    campaign = (
        db.query(Campaign)
        .filter(Campaign.id == int(campaign_id))
        .with_for_update()
        .first()
    )
    if campaign is None:
        raise PilotCohortRejected("CAMPAIGN_NOT_FOUND", "Campaign not found.")
    state = (
        db.query(CampaignPilotState)
        .filter(CampaignPilotState.campaign_id == int(campaign_id))
        .with_for_update()
        .first()
    )
    if not cohort_guard_active(state):
        raise PilotCohortRejected(
            "PILOT_LIMIT_REQUIRED",
            "ابتدا سقف مخاطب Pilot را ثبت کنید.",
        )
    existing = (
        db.query(CampaignPilotCohortMember.id)
        .filter(CampaignPilotCohortMember.campaign_id == int(campaign_id))
        .first()
    )
    if existing is not None:
        return pilot_progress_view(db, int(campaign_id))
    recipients = (
        db.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == int(campaign_id))
        .order_by(CampaignRecipient.id.asc())
        .limit(int(state.recipient_limit))
        .all()
    )
    for ordinal, recipient in enumerate(recipients, start=1):
        db.add(
            CampaignPilotCohortMember(
                campaign_id=int(campaign_id),
                campaign_recipient_id=int(recipient.id),
                contact_id=int(recipient.contact_id),
                ordinal=ordinal,
            )
        )
    db.flush()
    return pilot_progress_view(db, int(campaign_id))


def reset_pilot_cohort(
    db: Session,
    campaign_id: int,
    *,
    activity_probe=None,
) -> dict:
    """Drop membership only while the campaign is stopped and idle."""
    campaign = (
        db.query(Campaign)
        .filter(Campaign.id == int(campaign_id))
        .with_for_update()
        .first()
    )
    if campaign is None:
        raise PilotCohortRejected("CAMPAIGN_NOT_FOUND", "Campaign not found.")
    if campaign.status == CampaignStatus.RUNNING.value:
        raise PilotCohortRejected(
            "CAMPAIGN_RUNNING",
            "Pilot کمپین در حال اجرا بازنشانی نمی‌شود.",
        )
    queued = (
        db.query(StagedQueueItem.id)
        .filter(
            StagedQueueItem.campaign_id == int(campaign_id),
            StagedQueueItem.status.in_(
                (
                    StagedQueueItemStatus.QUEUED.value,
                    StagedQueueItemStatus.PUSHING.value,
                )
            ),
        )
        .first()
    )
    if queued is not None:
        raise PilotCohortRejected("CAMPAIGN_QUEUE_NONEMPTY", "Queue این کمپین خالی نیست.")
    probe = activity_probe
    if probe is None:
        from core_engine.services.campaign_reprepare import _redis_activity_probe

        probe = _redis_activity_probe
    if probe(int(campaign_id)):
        raise PilotCohortRejected(
            "CAMPAIGN_LIVE_ACTIVITY",
            "Queue، Lease یا Inflight این کمپین خالی نیست.",
        )
    (
        db.query(CampaignPilotCohortMember)
        .filter(CampaignPilotCohortMember.campaign_id == int(campaign_id))
        .delete(synchronize_session=False)
    )
    state = db.get(CampaignPilotState, int(campaign_id))
    if state is not None:
        state.enabled = False
        state.recipient_limit = None
        state.admin_resume_confirmed = False
    db.flush()
    return pilot_progress_view(db, int(campaign_id))


def pilot_progress_view(db: Session, campaign_id: int) -> dict:
    state = db.get(CampaignPilotState, int(campaign_id))
    members = (
        db.query(CampaignPilotCohortMember)
        .filter(CampaignPilotCohortMember.campaign_id == int(campaign_id))
        .order_by(CampaignPilotCohortMember.ordinal.asc())
        .all()
    )
    from core_engine.services.campaign_send_safety import success_count

    size = len(members)
    first_id = int(members[0].campaign_recipient_id) if members else None
    last_id = int(members[-1].campaign_recipient_id) if members else None
    attempted = 0
    remaining = 0
    if members:
        attempted = int(
            db.query(func.count(func.distinct(CampaignPilotCohortMember.contact_id)))
            .select_from(CampaignPilotCohortMember)
            .join(
                Message,
                (Message.campaign_id == CampaignPilotCohortMember.campaign_id)
                & (Message.contact_id == CampaignPilotCohortMember.contact_id),
            )
            .join(MessageAttempt, MessageAttempt.message_id == Message.id)
            .filter(CampaignPilotCohortMember.campaign_id == int(campaign_id))
            .scalar()
            or 0
        )
        remaining = int(
            db.query(CampaignRecipient.id)
            .join(
                CampaignPilotCohortMember,
                CampaignPilotCohortMember.campaign_recipient_id == CampaignRecipient.id,
            )
            .filter(
                CampaignPilotCohortMember.campaign_id == int(campaign_id),
                CampaignRecipient.send_status.notin_(COHORT_TERMINAL_SEND_STATUSES),
            )
            .count()
        )
    successes = success_count(db, int(campaign_id)) if state is not None else 0
    active = cohort_guard_active(state) and size > 0
    return {
        "pilot_recipient_limit": None if state is None else state.recipient_limit,
        "pilot_cohort_size": size,
        "pilot_first_recipient_id": first_id,
        "pilot_last_recipient_id": last_id,
        "pilot_unique_attempted": attempted,
        "pilot_success_count": successes,
        "pilot_remaining": remaining,
        "pilot_active": active,
    }


def pilot_blocks_new_success(db: Session, campaign_id: int, contact_id: int) -> bool:
    """Serialize the success cap. A contact outside the cohort is never counted."""
    state = (
        db.query(CampaignPilotState)
        .filter(CampaignPilotState.campaign_id == int(campaign_id))
        .with_for_update()
        .first()
    )
    if not cohort_guard_active(state):
        return False
    member = (
        db.query(CampaignPilotCohortMember.id)
        .filter(
            CampaignPilotCohortMember.campaign_id == int(campaign_id),
            CampaignPilotCohortMember.contact_id == int(contact_id),
        )
        .first()
    )
    if member is None:
        return True
    from core_engine.services.campaign_send_safety import success_count

    if int(state.confirmed) >= int(state.success_limit):
        return True
    return success_count(db, int(campaign_id)) >= int(state.success_limit)


def pause_if_cohort_exhausted(db: Session, campaign_id: int) -> bool:
    """Pause once every stored member has a terminal outcome and successes stay under the cap."""
    state = (
        db.query(CampaignPilotState)
        .filter(CampaignPilotState.campaign_id == int(campaign_id))
        .with_for_update()
        .first()
    )
    if not cohort_guard_active(state) or not bool(state.auto_pause):
        return False
    campaign = db.get(Campaign, int(campaign_id))
    if campaign is None or campaign.status != CampaignStatus.RUNNING.value:
        return False
    size = (
        db.query(CampaignPilotCohortMember.id)
        .filter(CampaignPilotCohortMember.campaign_id == int(campaign_id))
        .count()
    )
    if size <= 0:
        return False
    open_members = (
        db.query(CampaignRecipient.id)
        .join(
            CampaignPilotCohortMember,
            CampaignPilotCohortMember.campaign_recipient_id == CampaignRecipient.id,
        )
        .filter(
            CampaignPilotCohortMember.campaign_id == int(campaign_id),
            CampaignRecipient.send_status.notin_(COHORT_TERMINAL_SEND_STATUSES),
        )
        .count()
    )
    if open_members:
        return False
    campaign.status = CampaignStatus.PAUSED.value
    db.flush()
    return True


def connector_admission(
    db: Session,
    *,
    campaign_id: object,
    contact_id: object,
    kill_switch: bool,
    window_open: bool,
) -> str:
    """Return allow, stop, defer, or reject. Reject and stop do not call the connector."""
    if kill_switch:
        return "stop"
    if not idempotency_key_is_valid(campaign_id, contact_id):
        return "reject"
    cid = int(campaign_id)  # type: ignore[arg-type]
    ctid = int(contact_id)  # type: ignore[arg-type]
    campaign = db.get(Campaign, cid)
    if campaign is None or campaign.status != CampaignStatus.RUNNING.value:
        return "stop"
    if not window_open:
        return "defer"
    already = (
        db.query(CampaignSendSuccess.id)
        .filter(
            CampaignSendSuccess.campaign_id == cid,
            CampaignSendSuccess.contact_id == ctid,
        )
        .first()
    )
    if already is not None:
        return "reject"
    state = db.get(CampaignPilotState, cid)
    if cohort_guard_active(state):
        member = (
            db.query(CampaignPilotCohortMember.id)
            .filter(
                CampaignPilotCohortMember.campaign_id == cid,
                CampaignPilotCohortMember.contact_id == ctid,
            )
            .first()
        )
        if member is None:
            return "reject"
        from core_engine.services.campaign_send_safety import success_count

        if success_count(db, cid) >= int(state.success_limit):
            if state.auto_pause:
                campaign.status = CampaignStatus.PAUSED.value
                db.flush()
            return "stop"
    return "allow"


def admit_connector_call(
    db: Session,
    *,
    campaign_id: object,
    contact_id: object,
    kill_switch: bool,
    window_open: bool,
    connector,
) -> str:
    decision = connector_admission(
        db,
        campaign_id=campaign_id,
        contact_id=contact_id,
        kill_switch=kill_switch,
        window_open=window_open,
    )
    if decision == "allow":
        connector()
    return decision
