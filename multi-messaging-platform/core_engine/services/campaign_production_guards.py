"""Production safety guards for controlled Rubika campaigns (post-R10).

Fail-closed helpers used by prepare, campaign preflight, and queue bridge.
Never logs phones, tokens, or session material.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import (
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    Contact,
    Message,
    MessageAttempt,
    MessageAttemptStatus,
    PlatformType,
    SendStatus,
    StagedQueueItem,
)
from core_engine.services.campaign_render import remaining_placeholders
from core_engine.services.utils import normalize_phone_number

# --- Reason codes (operator-visible) ---
INVALID_RECIPIENT_PHONE = "INVALID_RECIPIENT_PHONE"
UNRESOLVED_TEMPLATE_PLACEHOLDER = "UNRESOLVED_TEMPLATE_PLACEHOLDER"
NO_WORKER_CONSUMER = "NO_WORKER_CONSUMER"
INVALID_MESSAGE_ASSIGNMENT = "INVALID_MESSAGE_ASSIGNMENT"
DUPLICATE_RECIPIENT_ASSIGNMENT = "DUPLICATE_RECIPIENT_ASSIGNMENT"
CAMPAIGN_SEND_LIMIT_REACHED = "CAMPAIGN_SEND_LIMIT_REACHED"
MESSAGE_ALREADY_SENT = "MESSAGE_ALREADY_SENT"
CAMPAIGN_NOT_RUNNING = "CAMPAIGN_NOT_RUNNING"

# Accidental single-brace placeholders (not the supported {{name}} form).
_SINGLE_BRACE_PLACEHOLDER = re.compile(
    r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})"
)

_IR_MOBILE_E164_DIGITS = re.compile(r"^98\d{10}$")


@dataclass(frozen=True)
class PhoneValidationResult:
    ok: bool
    reason_code: str | None = None
    digit_len: int = 0


@dataclass
class GuardIssue:
    code: str
    message: str
    message_id: int | None = None
    contact_id: int | None = None
    account_id: int | None = None
    placeholder_names: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def as_blocker(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.message_id is not None:
            out["message_id"] = self.message_id
        if self.contact_id is not None:
            out["contact_id"] = self.contact_id
        if self.account_id is not None:
            out["account_id"] = self.account_id
        if self.placeholder_names:
            out["placeholder_names"] = list(self.placeholder_names)
        if self.details:
            out["details"] = dict(self.details)
        return out


def is_valid_iran_mobile_e164(phone: str | None) -> PhoneValidationResult:
    """Require canonical IR mobile: digits-only length 12 starting with 98."""
    digits = normalize_phone_number(phone or "")
    if not digits:
        return PhoneValidationResult(False, INVALID_RECIPIENT_PHONE, 0)
    if not digits.isdigit():
        return PhoneValidationResult(False, INVALID_RECIPIENT_PHONE, len(digits))
    if not _IR_MOBILE_E164_DIGITS.fullmatch(digits):
        return PhoneValidationResult(False, INVALID_RECIPIENT_PHONE, len(digits))
    return PhoneValidationResult(True, None, len(digits))


def contact_phone_for_rubika(contact: Contact) -> str:
    return (contact.phone_e164 or contact.phone or "").strip()


def validate_rubika_contact_phone(contact: Contact) -> PhoneValidationResult:
    return is_valid_iran_mobile_e164(contact_phone_for_rubika(contact))


def find_unresolved_placeholders(text: str | None) -> list[str]:
    """Detect supported ``{{name}}`` leftovers and accidental ``{name}`` forms."""
    names: list[str] = []
    seen: set[str] = set()
    for name in remaining_placeholders(text or ""):
        if name not in seen:
            seen.add(name)
            names.append(name)
    for name in _SINGLE_BRACE_PLACEHOLDER.findall(text or ""):
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def controlled_production_enabled() -> bool:
    return bool(getattr(get_settings(), "CONTROLLED_PRODUCTION_ENABLED", False))


def resolve_campaign_max_total_messages(campaign: Campaign) -> int | None:
    """Return a stored campaign cap, or None when the campaign is uncapped.

    NULL ``max_contacts`` means no campaign-wide recipient ceiling. The former
    controlled-production default of 5 is not applied.
    """
    if campaign.max_contacts is None:
        return None
    return int(campaign.max_contacts)


def campaign_cap_view(campaign: Campaign) -> dict[str, int | bool | None]:
    cap = resolve_campaign_max_total_messages(campaign)
    return {"effective_cap": cap, "unlimited": cap is None}


def scan_campaign_recipient_phones(
    db: Session, campaign: Campaign
) -> tuple[list[int], list[GuardIssue]]:
    """Return (valid_contact_ids, invalid_issues) for Rubika campaigns."""
    if campaign.platform != PlatformType.RUBIKA:
        rows = (
            db.query(CampaignRecipient)
            .filter(CampaignRecipient.campaign_id == campaign.id)
            .all()
        )
        return [int(r.contact_id) for r in rows], []

    rows = (
        db.query(CampaignRecipient, Contact)
        .join(Contact, CampaignRecipient.contact_id == Contact.id)
        .filter(CampaignRecipient.campaign_id == campaign.id)
        .order_by(CampaignRecipient.id.asc())
        .all()
    )
    valid: list[int] = []
    issues: list[GuardIssue] = []
    for _recip, contact in rows:
        result = validate_rubika_contact_phone(contact)
        if result.ok:
            valid.append(int(contact.id))
        else:
            issues.append(
                GuardIssue(
                    code=INVALID_RECIPIENT_PHONE,
                    message="شماره گیرنده روبیکا معتبر نیست (باید 98 + 10 رقم باشد).",
                    contact_id=int(contact.id),
                    details={"digit_len": result.digit_len},
                )
            )
    return valid, issues


def scan_unresolved_placeholders_for_campaign(
    db: Session, campaign_id: int
) -> list[GuardIssue]:
    issues: list[GuardIssue] = []
    messages = (
        db.query(Message)
        .filter(Message.campaign_id == campaign_id)
        .order_by(Message.id.asc())
        .all()
    )
    for message in messages:
        names = find_unresolved_placeholders(message.rendered_text)
        if names:
            issues.append(
                GuardIssue(
                    code=UNRESOLVED_TEMPLATE_PLACEHOLDER,
                    message="متن نهایی پیام هنوز placeholder حل‌نشده دارد.",
                    message_id=int(message.id),
                    contact_id=int(message.contact_id),
                    placeholder_names=names,
                )
            )
    staged = (
        db.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign_id)
        .order_by(StagedQueueItem.id.asc())
        .all()
    )
    seen_msg = {i.message_id for i in issues if i.message_id is not None}
    for item in staged:
        names = find_unresolved_placeholders(item.final_text)
        if not names:
            continue
        mid = None
        if isinstance(item.queue_payload, dict):
            raw = item.queue_payload.get("message_id")
            if str(raw).isdigit():
                mid = int(raw)
        if mid is not None and mid in seen_msg:
            continue
        issues.append(
            GuardIssue(
                code=UNRESOLVED_TEMPLATE_PLACEHOLDER,
                message="متن staged هنوز placeholder حل‌نشده دارد.",
                message_id=mid,
                contact_id=int(item.contact_id),
                placeholder_names=names,
            )
        )
    return issues


def scan_assignment_consistency(
    db: Session, campaign: Campaign
) -> list[GuardIssue]:
    issues: list[GuardIssue] = []
    allowed_accounts = {
        int(link.account_id)
        for link in db.query(CampaignAccount)
        .filter(
            CampaignAccount.campaign_id == campaign.id,
            CampaignAccount.enabled.is_(True),
        )
        .all()
    }
    # Auto mode (no links): any platform account is out of scope for this guard;
    # require explicit CampaignAccount for production-controlled Rubika campaigns.
    messages = (
        db.query(Message)
        .filter(Message.campaign_id == campaign.id)
        .order_by(Message.id.asc())
        .all()
    )
    seen_contacts: dict[int, int] = {}
    for message in messages:
        if message.campaign_id != campaign.id:
            issues.append(
                GuardIssue(
                    code=INVALID_MESSAGE_ASSIGNMENT,
                    message="campaign_id پیام با کمپین هم‌خوان نیست.",
                    message_id=int(message.id),
                    contact_id=int(message.contact_id),
                )
            )
            continue
        if message.account_id is None or message.contact_id is None:
            issues.append(
                GuardIssue(
                    code=INVALID_MESSAGE_ASSIGNMENT,
                    message="پیام بدون account_id یا contact_id است.",
                    message_id=int(message.id),
                    contact_id=int(message.contact_id) if message.contact_id else None,
                )
            )
            continue
        if allowed_accounts and int(message.account_id) not in allowed_accounts:
            issues.append(
                GuardIssue(
                    code=INVALID_MESSAGE_ASSIGNMENT,
                    message="اکانت پیام در لیست فرستنده‌های کمپین نیست.",
                    message_id=int(message.id),
                    contact_id=int(message.contact_id),
                    account_id=int(message.account_id),
                )
            )
        prior = seen_contacts.get(int(message.contact_id))
        if prior is not None and prior != int(message.id):
            issues.append(
                GuardIssue(
                    code=DUPLICATE_RECIPIENT_ASSIGNMENT,
                    message="برای یک گیرنده بیش از یک پیام آماده‌شده وجود دارد.",
                    message_id=int(message.id),
                    contact_id=int(message.contact_id),
                    details={"other_message_id": prior},
                )
            )
        else:
            seen_contacts[int(message.contact_id)] = int(message.id)
    return issues


def message_has_terminal_success(db: Session, message_id: int) -> bool:
    recip = (
        db.query(CampaignRecipient)
        .filter(CampaignRecipient.final_message_id == message_id)
        .first()
    )
    if recip is not None and recip.send_status in {
        SendStatus.DELIVERED,
        SendStatus.READ,
        SendStatus.ACCEPTED_BY_PLATFORM,
        SendStatus.UNKNOWN_EXTERNAL_RESULT,
    }:
        return True
    att = (
        db.query(MessageAttempt)
        .filter(
            MessageAttempt.message_id == message_id,
            MessageAttempt.status == MessageAttemptStatus.SUCCESS,
        )
        .first()
    )
    return att is not None


def count_campaign_success_messages(db: Session, campaign_id: int) -> int:
    message_ids = [
        int(r[0])
        for r in db.query(Message.id).filter(Message.campaign_id == campaign_id).all()
    ]
    if not message_ids:
        return 0
    return int(
        db.query(MessageAttempt)
        .filter(
            MessageAttempt.message_id.in_(message_ids),
            MessageAttempt.status == MessageAttemptStatus.SUCCESS,
        )
        .distinct(MessageAttempt.message_id)
        .count()
    )


def evaluate_send_limit(
    db: Session, campaign: Campaign, *, prepared_or_ready_count: int
) -> GuardIssue | None:
    limit = resolve_campaign_max_total_messages(campaign)
    if limit is None:
        return None
    if prepared_or_ready_count > int(limit):
        return GuardIssue(
            code=CAMPAIGN_SEND_LIMIT_REACHED,
            message=f"سقف ارسال کمپین ({limit}) رد شده است.",
            details={
                "max_total_messages": int(limit),
                "prepared_or_ready_count": int(prepared_or_ready_count),
            },
        )
    return None


def count_campaign_dispatch_progress(db: Session, campaign_id: int) -> int:
    """Messages already counting toward the campaign send cap (success + in-flight)."""
    from core_engine.models import StagedQueueItemStatus

    success_ids = {
        int(r[0])
        for r in db.query(MessageAttempt.message_id)
        .join(Message, Message.id == MessageAttempt.message_id)
        .filter(
            Message.campaign_id == campaign_id,
            MessageAttempt.status == MessageAttemptStatus.SUCCESS,
        )
        .all()
    }
    in_flight_statuses = {
        StagedQueueItemStatus.QUEUED.value,
        StagedQueueItemStatus.PUSHING.value,
    }
    staged_ids = {
        int(payload.get("message_id"))
        for item in db.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == campaign_id,
            StagedQueueItem.status.in_(in_flight_statuses),
        )
        .all()
        if isinstance((payload := item.queue_payload), dict)
        and str(payload.get("message_id", "")).isdigit()
    }
    recip_ids = {
        int(r.final_message_id)
        for r in db.query(CampaignRecipient)
        .filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.final_message_id.isnot(None),
            CampaignRecipient.send_status.in_(
                {
                    SendStatus.QUEUED,
                    SendStatus.PROCESSING,
                    SendStatus.ACCEPTED_BY_WORKER,
                    SendStatus.ACCEPTED_BY_PLATFORM,
                    SendStatus.DELIVERED,
                    SendStatus.READ,
                }
            ),
        )
        .all()
    }
    return len(success_ids | staged_ids | recip_ids)


def would_exceed_send_limit(
    db: Session, campaign: Campaign, *, additional: int = 1
) -> GuardIssue | None:
    limit = resolve_campaign_max_total_messages(campaign)
    if limit is None:
        return None
    progress = count_campaign_dispatch_progress(db, int(campaign.id))
    if progress + int(additional) > int(limit):
        return GuardIssue(
            code=CAMPAIGN_SEND_LIMIT_REACHED,
            message=f"سقف ارسال کمپین ({limit}) رد شده است.",
            details={
                "max_total_messages": int(limit),
                "dispatch_progress": int(progress),
            },
        )
    return None


def build_execution_metrics(
    *,
    total_recipients: int,
    valid_recipients: int,
    invalid_recipients: int,
    prepared_messages: int,
    queued_messages: int,
    sending_messages: int,
    success_messages: int,
    failed_retryable: int,
    failed_permanent: int,
    assigned_accounts: int,
    ready_accounts: int,
    worker_coverage_accounts: int,
    block_code: str | None,
    block_details: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "total_recipients": total_recipients,
        "valid_recipients": valid_recipients,
        "invalid_recipients": invalid_recipients,
        "prepared_messages": prepared_messages,
        "queued_messages": queued_messages,
        "sending_messages": sending_messages,
        "success_messages": success_messages,
        "failed_retryable": failed_retryable,
        "failed_permanent": failed_permanent,
        "assigned_accounts": assigned_accounts,
        "ready_accounts": ready_accounts,
        "worker_coverage_accounts": worker_coverage_accounts,
        "block_code": block_code,
        "block_details": block_details,
    }
