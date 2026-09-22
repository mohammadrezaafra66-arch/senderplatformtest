"""Campaign sender resolution and canonical automatic CampaignAccount assignment.

Assignment readiness (stable) is independent of execution readiness (worker/redis).
Persisting CampaignAccount must not require a live worker. Start/Dispatch still do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    CampaignStatus,
    ChannelSession,
    Message,
    PlatformType,
    RenderedMessage,
    RubikaAccountPool,
    RubikaIdentityStatus,
    RubikaSessionStatus,
    SessionType,
    StagedQueueItem,
)
from core_engine.services.campaign_sender_eligibility import filter_auto_select_eligible
from core_engine.services.rubika_account_activation import (
    ACTIVATION_PENDING,
    NOT_REQUIRED,
    READY_TO_SEND,
    send_activation_state,
)
from core_engine.services.rubika_l17_automation import account_is_canonical_managed

_SEND_ASSIGNMENT_PHASES = frozenset({"day", "night"})
_BACKFILL_BLOCKED_STATUSES = frozenset(
    {
        CampaignStatus.RUNNING.value,
        CampaignStatus.COMPLETED.value,
        CampaignStatus.CANCELLED.value,
        CampaignStatus.ARCHIVED.value,
        CampaignStatus.FAILED.value,
        CampaignStatus.PREPARED.value,
        CampaignStatus.PAUSED.value,
    }
)


def _sender_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"code": code, "message": message},
    )


def resolve_campaign_sender_accounts(
    db: Session,
    campaign: Campaign,
    *,
    persist_required: bool = False,
) -> list[Account]:
    """Return one validated sender pool; an empty relation set means Auto mode.

    Prepare must pass persist_required=True so only current CampaignAccount rows
    are used. Auto mode (empty links) selects campaign_eligible=True accounts
    and is not allowed for message preparation.
    Manual/persisted links return assigned enabled accounts (ASSIGNED_BUT_NOT_READY
    allowed); start/preflight gates still block non-ready senders.
    """
    links = (
        db.query(CampaignAccount)
        .filter(CampaignAccount.campaign_id == campaign.id)
        .order_by(CampaignAccount.priority.asc(), CampaignAccount.id.asc())
        .all()
    )
    if not links:
        if persist_required:
            raise _sender_error(
                "no_campaign_sender_account",
                "No sender account is assigned to this campaign.",
            )
        accounts = (
            db.query(Account)
            .filter(
                Account.status == AccountStatus.ACTIVE,
                Account.platform == campaign.platform,
                Account.archived_at.is_(None),
            )
            .order_by(Account.id.asc())
            .all()
        )
        if not accounts:
            raise _sender_error(
                "no_active_sender_account",
                f"No active sender account is available for {campaign.platform.value}.",
            )
        eligible = filter_auto_select_eligible(db, accounts, campaign=campaign)
        if not eligible:
            raise _sender_error(
                "no_ready_sender_account",
                f"No campaign-eligible sender account is available for {campaign.platform.value}.",
            )
        return eligible

    enabled_links = [link for link in links if link.enabled]
    if not enabled_links:
        raise _sender_error(
            "no_enabled_campaign_sender",
            "Campaign has manual sender accounts, but none are enabled.",
        )

    account_ids = [link.account_id for link in enabled_links]
    accounts_by_id = {
        account.id: account
        for account in db.query(Account).filter(Account.id.in_(account_ids)).all()
    }
    result: list[Account] = []
    for link in enabled_links:
        account = accounts_by_id.get(link.account_id)
        if account is None:
            raise _sender_error(
                "campaign_sender_missing",
                f"Campaign sender account {link.account_id} no longer exists.",
            )
        if account.status != AccountStatus.ACTIVE:
            raise _sender_error(
                "campaign_sender_inactive",
                f"Campaign sender account {account.id} is not active.",
            )
        if account.platform != campaign.platform:
            raise _sender_error(
                "campaign_sender_platform_mismatch",
                f"Campaign sender account {account.id} does not match {campaign.platform.value}.",
            )
        if getattr(account, "archived_at", None) is not None:
            raise _sender_error(
                "campaign_sender_inactive",
                f"Campaign sender account {account.id} is not active.",
            )
        result.append(account)
    if persist_required and not result:
        raise _sender_error(
            "no_ready_campaign_sender_account",
            "No allowed ready sender account is assigned to this campaign.",
        )
    return result


def resync_campaign_prepared_senders(db: Session, campaign: Campaign) -> None:
    """Map existing prepared artifacts onto current CampaignAccount rows.

    Updates Message.account_id and queue payload account_id only. Does not
    create messages, change campaign status, or start delivery.
    """
    from sqlalchemy.orm.attributes import flag_modified

    try:
        senders = resolve_campaign_sender_accounts(db, campaign, persist_required=True)
    except HTTPException:
        return
    if not senders:
        return

    recipients = (
        db.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == campaign.id)
        .order_by(CampaignRecipient.id.asc())
        .all()
    )
    index_by_contact = {int(row.contact_id): index for index, row in enumerate(recipients)}

    def assigned_id_for_contact(contact_id: int | None) -> int:
        index = index_by_contact.get(int(contact_id or 0), 0)
        return int(senders[index % len(senders)].id)

    messages = (
        db.query(Message)
        .filter(Message.campaign_id == campaign.id)
        .order_by(Message.id.asc())
        .all()
    )
    for message in messages:
        new_id = assigned_id_for_contact(message.contact_id)
        if int(message.account_id) != new_id:
            message.account_id = new_id

    staged_items = (
        db.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign.id)
        .all()
    )
    for item in staged_items:
        payload = dict(item.queue_payload or {})
        new_id = assigned_id_for_contact(item.contact_id)
        if payload.get("account_id") != new_id:
            payload["account_id"] = new_id
            item.queue_payload = payload
            flag_modified(item, "queue_payload")

    rendered_items = (
        db.query(RenderedMessage)
        .filter(RenderedMessage.campaign_id == campaign.id)
        .all()
    )
    for rendered in rendered_items:
        payload = dict(rendered.queue_payload or {}) if rendered.queue_payload else {}
        if not payload:
            continue
        new_id = assigned_id_for_contact(rendered.contact_id)
        if payload.get("account_id") != new_id:
            payload["account_id"] = new_id
            rendered.queue_payload = payload
            flag_modified(rendered, "queue_payload")


def resolve_assignment_target_phase(db: Session) -> str:
    """Send-pool phase used for assignment. Off-window does not block assignment."""
    from core_engine.config import get_settings
    from workers.rubika_account_pool import resolve_current_phase

    current = resolve_current_phase(db)
    if current in _SEND_ASSIGNMENT_PHASES:
        return str(current)
    return str(getattr(get_settings(), "DEFAULT_RUBIKA_POOL", "day") or "day")


def _identity_verified(account: Account) -> bool:
    status = account.rubika_identity_status
    if status in {None, RubikaIdentityStatus.UNBOUND, RubikaIdentityStatus.MISMATCH_LOCKED}:
        return False
    return status in {RubikaIdentityStatus.VERIFIED, RubikaIdentityStatus.OPERATOR_OVERRIDE}


def _pool_phases(db: Session, account_id: int) -> list[str]:
    rows = (
        db.query(RubikaAccountPool.phase)
        .filter(RubikaAccountPool.account_id == int(account_id))
        .order_by(RubikaAccountPool.phase.asc())
        .all()
    )
    return [str(phase) for (phase,) in rows]


def _active_session_count(db: Session, account_id: int) -> int:
    return (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == int(account_id),
            ChannelSession.session_type == SessionType.RUBIKA_SESSION,
            ChannelSession.session_status == RubikaSessionStatus.ACTIVE,
        )
        .count()
    )


def classify_stable_assignment_account(
    db: Session,
    account: Account,
    *,
    target_phase: str | None = None,
) -> "StableAssignmentAccountView":
    """Stable assignment eligibility. Ignores worker, redis, quota, dispatch."""
    phase = target_phase or resolve_assignment_target_phase(db)
    pool_phases = _pool_phases(db, int(account.id))
    pool_label = ",".join(pool_phases) if pool_phases else "none"
    session_count = _active_session_count(db, int(account.id))
    session_label = "active" if session_count == 1 else ("missing" if session_count == 0 else "invalid")
    activation = send_activation_state(db, int(account.id))
    if activation == READY_TO_SEND:
        activation_label = "confirmed"
    elif activation == NOT_REQUIRED:
        activation_label = "not_required"
    else:
        activation_label = "pending"

    reason: str | None = None
    if account.platform != PlatformType.RUBIKA:
        reason = "PLATFORM_MISMATCH"
    elif getattr(account, "archived_at", None) is not None:
        reason = "ACCOUNT_ARCHIVED"
        session_label = session_label if session_count else "missing"
    elif account.status == AccountStatus.REQUIRES_LOGIN:
        reason = "LOGIN_REQUIRED"
    elif account.status != AccountStatus.ACTIVE:
        reason = "ACCOUNT_INACTIVE"
    elif not account_is_canonical_managed(db, int(account.id)):
        if session_count != 1:
            reason = "NO_CANONICAL_SESSION"
            session_label = "missing" if session_count == 0 else "invalid"
        elif not _identity_verified(account):
            reason = "IDENTITY_UNVERIFIED"
        else:
            reason = "NO_CANONICAL_SESSION"
            session_label = "invalid"
    elif not _identity_verified(account):
        reason = "IDENTITY_UNVERIFIED"
    elif activation == ACTIVATION_PENDING:
        reason = "ACTIVATION_UNCONFIRMED"
    elif not pool_phases:
        reason = "NOT_IN_POOL"
    elif phase not in pool_phases:
        reason = "PHASE_NOT_ALLOWED"
    elif phase not in _SEND_ASSIGNMENT_PHASES:
        reason = "PHASE_NOT_ALLOWED"

    return StableAssignmentAccountView(
        account_id=int(account.id),
        eligible=reason is None,
        session=session_label,
        activation=activation_label,
        pool="member" if pool_phases else "none",
        phase=phase if phase in pool_phases else (pool_phases[0] if pool_phases else "none"),
        reason_excluded=reason,
    )


def list_stable_assignment_eligible_accounts(
    db: Session,
    *,
    target_phase: str | None = None,
) -> tuple[list[Account], list["StableAssignmentAccountView"]]:
    """Canonical query: Rubika accounts that may be persisted onto a campaign."""
    phase = target_phase or resolve_assignment_target_phase(db)
    accounts = (
        db.query(Account)
        .filter(Account.platform == PlatformType.RUBIKA)
        .order_by(Account.id.asc())
        .all()
    )
    views = [
        classify_stable_assignment_account(db, account, target_phase=phase)
        for account in accounts
    ]
    by_id = {account.id: account for account in accounts}
    eligible = [by_id[view.account_id] for view in views if view.eligible]
    return eligible, views


@dataclass
class StableAssignmentAccountView:
    account_id: int
    eligible: bool
    session: str
    activation: str
    pool: str
    phase: str
    reason_excluded: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "stable_assignment_eligible": self.eligible,
            "session": self.session,
            "activation": self.activation,
            "pool": self.pool,
            "phase": self.phase,
            "reason_excluded": self.reason_excluded,
        }


@dataclass
class AutomaticSenderAssignmentResult:
    mode: str = "automatic"
    eligible_accounts: int = 0
    existing_assignments: int = 0
    created_assignments: int = 0
    skipped_assignments: int = 0
    excluded_accounts: int = 0
    reason: str | None = None
    eligible_account_ids: list[int] = field(default_factory=list)
    created_account_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "eligible_accounts": self.eligible_accounts,
            "existing_assignments": self.existing_assignments,
            "created_assignments": self.created_assignments,
            "skipped_assignments": self.skipped_assignments,
            "excluded_accounts": self.excluded_accounts,
            "reason": self.reason,
            "eligible_account_ids": list(self.eligible_account_ids),
            "created_account_ids": list(self.created_account_ids),
        }


def _existing_campaign_account_ids(db: Session, campaign_id: int) -> set[int]:
    rows = (
        db.query(CampaignAccount.account_id)
        .filter(CampaignAccount.campaign_id == int(campaign_id))
        .all()
    )
    return {int(account_id) for (account_id,) in rows}


def _max_priority(db: Session, campaign_id: int) -> int:
    value = (
        db.query(func.max(CampaignAccount.priority))
        .filter(CampaignAccount.campaign_id == int(campaign_id))
        .scalar()
    )
    return int(value or 0)


def ensure_automatic_campaign_sender_assignments(
    db: Session,
    campaign: Campaign,
    *,
    dry_run: bool = False,
) -> AutomaticSenderAssignmentResult:
    """Persist stable-eligible Rubika senders. Idempotent. Never deletes existing links.

    Does not create messages, queue items, or change campaign status.
    Worker coverage is not required.
    """
    eligible, views = list_stable_assignment_eligible_accounts(db)
    excluded = sum(1 for view in views if not view.eligible)
    eligible_ids = [int(account.id) for account in eligible]
    existing = _existing_campaign_account_ids(db, int(campaign.id))
    existing_eligible = existing.intersection(eligible_ids)
    missing_ids = [account_id for account_id in eligible_ids if account_id not in existing]

    result = AutomaticSenderAssignmentResult(
        mode="automatic",
        eligible_accounts=len(eligible_ids),
        existing_assignments=len(existing),
        created_assignments=0,
        skipped_assignments=len(existing_eligible),
        excluded_accounts=excluded,
        eligible_account_ids=eligible_ids,
    )

    if campaign.platform != PlatformType.RUBIKA:
        result.mode = "skipped"
        result.reason = "NOT_RUBIKA"
        result.eligible_accounts = 0
        result.eligible_account_ids = []
        result.skipped_assignments = 0
        result.excluded_accounts = 0
        return result

    if not eligible_ids:
        result.reason = "NO_STABLE_SENDERS"
        return result

    if dry_run:
        result.reason = "DRY_RUN"
        return result

    if not missing_ids:
        result.reason = "ALREADY_ASSIGNED"
        return result

    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(campaign.id)})
    existing = _existing_campaign_account_ids(db, int(campaign.id))
    missing_ids = [account_id for account_id in eligible_ids if account_id not in existing]
    result.existing_assignments = len(existing)
    result.skipped_assignments = len(existing.intersection(eligible_ids))
    if not missing_ids:
        result.reason = "ALREADY_ASSIGNED"
        return result

    start_priority = _max_priority(db, int(campaign.id))
    rows = [
        {
            "campaign_id": int(campaign.id),
            "account_id": account_id,
            "priority": start_priority + index,
            "weight": 1,
            "enabled": True,
        }
        for index, account_id in enumerate(missing_ids, start=1)
    ]
    stmt = (
        pg_insert(CampaignAccount)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_campaign_accounts_campaign_id_account_id")
        .returning(CampaignAccount.account_id)
    )
    created = [int(account_id) for (account_id,) in db.execute(stmt).fetchall()]
    db.flush()
    db.expire(campaign, ["campaign_accounts"])
    result.created_assignments = len(created)
    result.created_account_ids = created
    result.skipped_assignments = len(eligible_ids) - len(created)
    if result.created_assignments == 0:
        result.reason = "ALREADY_ASSIGNED"
    else:
        result.reason = "ASSIGNED"
    return result


def campaign_counts(db: Session, campaign_id: int) -> dict[str, int]:
    recipients = (
        db.query(func.count(CampaignRecipient.id))
        .filter(CampaignRecipient.campaign_id == int(campaign_id))
        .scalar()
        or 0
    )
    messages = (
        db.query(func.count(Message.id)).filter(Message.campaign_id == int(campaign_id)).scalar()
        or 0
    )
    queue = (
        db.query(func.count(StagedQueueItem.id))
        .filter(StagedQueueItem.campaign_id == int(campaign_id))
        .scalar()
        or 0
    )
    accounts = (
        db.query(func.count(CampaignAccount.id))
        .filter(CampaignAccount.campaign_id == int(campaign_id))
        .scalar()
        or 0
    )
    return {
        "recipients": int(recipients),
        "messages": int(messages),
        "queue": int(queue),
        "existing_accounts": int(accounts),
    }


def classify_campaign_for_automatic_backfill(
    db: Session,
    campaign: Campaign,
) -> "CampaignBackfillView":
    counts = campaign_counts(db, int(campaign.id))
    existing = counts["existing_accounts"]
    sender_mode = "manual" if existing > 0 else "automatic"
    reason: str | None = None
    eligible = False
    platform = campaign.platform.value if hasattr(campaign.platform, "value") else str(campaign.platform)
    status = str(campaign.status or "")
    if campaign.platform != PlatformType.RUBIKA:
        reason = "NOT_RUBIKA"
    elif getattr(campaign, "archived_at", None) is not None:
        reason = "ARCHIVED"
    elif status in _BACKFILL_BLOCKED_STATUSES:
        reason = f"STATUS_{status.upper()}"
    elif status != CampaignStatus.DRAFT.value:
        reason = f"STATUS_{status.upper()}"
    elif sender_mode != "automatic":
        reason = "MANUAL_OR_EXISTING_ASSIGNMENT"
    elif counts["recipients"] <= 0:
        reason = "NO_RECIPIENTS"
    elif counts["messages"] > 0:
        reason = "HAS_MESSAGES"
    elif counts["queue"] > 0:
        reason = "HAS_QUEUE"
    else:
        eligible = True
        reason = "ELIGIBLE"

    return CampaignBackfillView(
        campaign_id=int(campaign.id),
        platform=platform,
        status=status,
        sender_mode=sender_mode,
        recipients=counts["recipients"],
        messages=counts["messages"],
        queue=counts["queue"],
        existing_accounts=existing,
        eligible_for_backfill=eligible,
        reason=reason or "UNKNOWN",
    )


@dataclass
class CampaignBackfillView:
    campaign_id: int
    platform: str
    status: str
    sender_mode: str
    recipients: int
    messages: int
    queue: int
    existing_accounts: int
    eligible_for_backfill: bool
    reason: str
    created: int = 0
    after: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "platform": self.platform,
            "status": self.status,
            "sender_mode": self.sender_mode,
            "recipients": self.recipients,
            "messages": self.messages,
            "queue": self.queue,
            "existing_accounts": self.existing_accounts,
            "eligible_for_backfill": self.eligible_for_backfill,
            "reason": self.reason,
            "created": self.created,
            "after": self.after,
        }


def dry_run_automatic_rubika_sender_backfill(db: Session) -> dict[str, Any]:
    """Read-only report of Rubika campaigns and stable-assignment accounts."""
    campaigns = (
        db.query(Campaign)
        .filter(Campaign.platform == PlatformType.RUBIKA)
        .order_by(Campaign.id.asc())
        .all()
    )
    campaign_views = [classify_campaign_for_automatic_backfill(db, campaign) for campaign in campaigns]
    eligible_accounts, account_views = list_stable_assignment_eligible_accounts(db)
    return {
        "assignment_phase": resolve_assignment_target_phase(db),
        "rubika_campaign_count": len(campaign_views),
        "eligible_campaign_count": sum(1 for view in campaign_views if view.eligible_for_backfill),
        "rejected_campaign_count": sum(
            1 for view in campaign_views if not view.eligible_for_backfill
        ),
        "candidate_account_count": len(eligible_accounts),
        "candidate_account_ids": [int(account.id) for account in eligible_accounts],
        "campaigns": [view.to_dict() for view in campaign_views],
        "accounts": [view.to_dict() for view in account_views],
        "rejection_reasons": _reason_counts(
            view.reason for view in campaign_views if not view.eligible_for_backfill
        ),
    }


def _reason_counts(reasons) -> dict[str, int]:
    counts: dict[str, int] = {}
    for reason in reasons:
        key = str(reason or "UNKNOWN")
        counts[key] = counts.get(key, 0) + 1
    return counts


def backfill_automatic_rubika_campaign_senders(
    db: Session,
    *,
    commit: bool = True,
) -> list[CampaignBackfillView]:
    """Create missing CampaignAccount rows for currently eligible automatic drafts.

    One transaction per campaign. Existing links are never deleted. Status/messages/queue
    are not mutated.
    """
    campaigns = (
        db.query(Campaign)
        .filter(Campaign.platform == PlatformType.RUBIKA)
        .order_by(Campaign.id.asc())
        .all()
    )
    results: list[CampaignBackfillView] = []
    for campaign in campaigns:
        view = classify_campaign_for_automatic_backfill(db, campaign)
        view.after = view.existing_accounts
        if not view.eligible_for_backfill:
            results.append(view)
            continue
        try:
            assigned = ensure_automatic_campaign_sender_assignments(db, campaign)
            view.created = assigned.created_assignments
            view.after = view.existing_accounts + assigned.created_assignments
            if commit:
                db.commit()
            else:
                db.flush()
        except Exception:
            db.rollback()
            raise
        results.append(view)
    return results
