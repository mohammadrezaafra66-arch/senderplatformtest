"""C1 — Authoritative campaign sender eligibility (consumes L18 runtime truth).

enabled/lifecycle ACTIVE alone is never sufficient for campaign_eligible.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from core_engine.models import Account, Campaign
from core_engine.services.account_runtime_status import (
    RuntimeStatus,
    compute_account_runtime_status,
    compute_all_account_runtime_statuses,
)
from core_engine.services.campaign_capacity import TEMPORARY_CODES
from core_engine.services.campaign_execution_labels import execution_blocker_label as format_execution_blocker_label

# Campaign-facing Persian labels (may be more explicit than Accounts-page L18 labels).
CAMPAIGN_SENDER_STATUS_LABEL_FA: dict[str, str] = {
    "READY": "آماده ارسال",
    "LOGIN_REQUIRED": "نیاز به ورود",
    "OTP_WAITING": "در انتظار کد",
    "AUTHENTICATING": "در حال احراز",
    "MANUAL_REVIEW": "نیازمند بررسی",
    "SESSION_ERROR": "خطای سشن",
    "CONNECTION_ERROR": "خطای اتصال",
    "AUTHENTICATED_NO_WORKER": "متصل، Worker آماده نیست",
    "DISABLED": "غیرفعال",
    "CAPACITY_EXHAUSTED": "ظرفیت تکمیل شده",
    "CIRCUIT_BLOCKED": "موقتاً مسدود",
    "CONFIG_ERROR": "خطای پیکربندی",
    "NOT_APPLICABLE": "نامرتبط",
    "PLATFORM_MISMATCH": "پلتفرم ناسازگار",
    "ACCOUNT_ARCHIVED": "آرشیو شده",
}

_RUNTIME_BLOCKERS: dict[str, str] = {
    RuntimeStatus.DISABLED.value: "DISABLED",
    RuntimeStatus.LOGIN_REQUIRED.value: "LOGIN_REQUIRED",
    RuntimeStatus.OTP_WAITING.value: "OTP_WAITING",
    RuntimeStatus.AUTHENTICATING.value: "AUTHENTICATING",
    RuntimeStatus.MANUAL_REVIEW.value: "MANUAL_REVIEW",
    RuntimeStatus.SESSION_ERROR.value: "SESSION_ERROR",
    RuntimeStatus.CONNECTION_ERROR.value: "CONNECTION_ERROR",
    RuntimeStatus.AUTHENTICATED_NO_WORKER.value: "AUTHENTICATED_NO_WORKER",
    RuntimeStatus.CONFIG_ERROR.value: "CONFIG_ERROR",
    RuntimeStatus.NOT_APPLICABLE.value: "NOT_APPLICABLE",
}

_CAPACITY_EXHAUSTED_CODES = frozenset(
    {
        "DAILY_CAP_REACHED",
        "HOURLY_CAP_REACHED",
        "CAPACITY_EXHAUSTED",
    }
)
_CIRCUIT_CODES = frozenset(
    {
        "RUBIKA_CIRCUIT_OPEN",
        "CIRCUIT_BLOCKED",
        "CIRCUIT_OPEN",
    }
)


@dataclass(slots=True)
class CampaignSenderEligibility:
    account_id: int
    platform: str
    display_identity: str
    enabled: bool
    runtime_status: str
    runtime_status_label: str
    auth_ready: bool
    worker_ready: bool
    dispatch_ready: bool
    campaign_eligible: bool
    blocker_code: str | None
    blocker_label: str | None
    execution_ready: bool = False
    execution_blocker_code: str | None = None
    execution_blocker_label: str | None = None
    daily_remaining: int | None = None
    hourly_remaining: int | None = None
    next_send_at: str | None = None
    last_verified_at: str | None = None
    assigned: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_api_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("details", None)
        return d


def resolve_display_identity(
    *,
    account_id: int,
    phone_number: str | None,
    label: str | None,
) -> str:
    """Never use index/count/boolean as identity."""
    phone = str(phone_number or "").strip()
    lab = str(label or "").strip()
    if phone and not _looks_like_non_identity(phone):
        return phone
    if lab and not _looks_like_non_identity(lab):
        return lab
    return f"اکانت #{int(account_id)}"


def _looks_like_non_identity(value: str) -> bool:
    v = value.strip()
    if not v:
        return True
    if v.lower() in {"true", "false", "none", "null"}:
        return True
    compact = v.replace(" ", "")
    if compact.isdigit() and len(compact) <= 3:
        return True
    if "/" in compact:
        parts = compact.split("/")
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            return True
    return False


def campaign_status_label(status: str, backend_label: str | None = None) -> str:
    if status == RuntimeStatus.AUTHENTICATED_NO_WORKER.value:
        return CAMPAIGN_SENDER_STATUS_LABEL_FA[status]
    if status in CAMPAIGN_SENDER_STATUS_LABEL_FA:
        return CAMPAIGN_SENDER_STATUS_LABEL_FA[status]
    if backend_label and backend_label.strip():
        return backend_label
    return status


def evaluate_campaign_sender_eligibility(
    db: Session | None,
    account: Account,
    *,
    campaign: Campaign | None = None,
    runtime=None,
    capacity_block_code: str | None = None,
    daily_remaining: int | None = None,
    hourly_remaining: int | None = None,
    next_send_at: str | None = None,
    assigned: bool = False,
) -> CampaignSenderEligibility:
    """Single authoritative eligibility predicate for manual + automatic selection."""
    if runtime is None:
        if db is None:
            raise ValueError("db is required when runtime is not provided")
        runtime = compute_account_runtime_status(db, account)

    platform = (
        account.platform.value if hasattr(account.platform, "value") else str(account.platform)
    )
    display = resolve_display_identity(
        account_id=int(account.id),
        phone_number=account.phone_number,
        label=account.label,
    )
    enabled = bool(runtime.enabled)
    l18_status = str(runtime.runtime_status)
    runtime_status = l18_status
    label = campaign_status_label(l18_status, getattr(runtime, "runtime_status_label", None))

    auth_ready = l18_status in {
        RuntimeStatus.READY.value,
        RuntimeStatus.AUTHENTICATED_NO_WORKER.value,
    }
    worker_ready = bool(runtime.worker_covered) and l18_status == RuntimeStatus.READY.value
    dispatch_ready = bool(runtime.dispatch_ready) and l18_status == RuntimeStatus.READY.value

    blocker_code: str | None = None
    blocker_label: str | None = None
    campaign_eligible = False

    if getattr(account, "archived_at", None) is not None:
        blocker_code = "ACCOUNT_ARCHIVED"
        runtime_status = "ACCOUNT_ARCHIVED"
        label = CAMPAIGN_SENDER_STATUS_LABEL_FA["ACCOUNT_ARCHIVED"]
    elif campaign is not None and getattr(campaign, "archived_at", None) is not None:
        blocker_code = "CAMPAIGN_ARCHIVED"
        label = "کمپین آرشیو شده"
    elif campaign is not None and account.platform != campaign.platform:
        blocker_code = "PLATFORM_MISMATCH"
    elif not enabled or l18_status == RuntimeStatus.DISABLED.value:
        blocker_code = "DISABLED"
    elif l18_status in _RUNTIME_BLOCKERS and l18_status != RuntimeStatus.READY.value:
        blocker_code = _RUNTIME_BLOCKERS[l18_status]
    elif not dispatch_ready:
        blocker_code = runtime.dispatch_blocker or "DISPATCH_NOT_READY"
    else:
        cap = (capacity_block_code or "").strip().upper() or None
        if cap in _CIRCUIT_CODES:
            blocker_code = "CIRCUIT_BLOCKED"
            runtime_status = "CIRCUIT_BLOCKED"
            label = CAMPAIGN_SENDER_STATUS_LABEL_FA["CIRCUIT_BLOCKED"]
        elif cap in _CAPACITY_EXHAUSTED_CODES or (
            daily_remaining is not None and daily_remaining <= 0
        ) or (hourly_remaining is not None and hourly_remaining <= 0):
            blocker_code = "CAPACITY_EXHAUSTED"
            runtime_status = "CAPACITY_EXHAUSTED"
            label = CAMPAIGN_SENDER_STATUS_LABEL_FA["CAPACITY_EXHAUSTED"]
        else:
            campaign_eligible = True

    if blocker_code and not campaign_eligible:
        blocker_label = CAMPAIGN_SENDER_STATUS_LABEL_FA.get(blocker_code, label or blocker_code)
        if blocker_code in CAMPAIGN_SENDER_STATUS_LABEL_FA:
            label = CAMPAIGN_SENDER_STATUS_LABEL_FA[blocker_code]

    execution_blocker_code: str | None = None
    execution_blocker_label: str | None = None
    if campaign_eligible:
        cap = (capacity_block_code or "").strip().upper() or None
        if cap in TEMPORARY_CODES:
            execution_blocker_code = cap
            execution_blocker_label = format_execution_blocker_label(
                cap, next_send_at=next_send_at
            )
    execution_ready = bool(
        campaign_eligible
        and dispatch_ready
        and execution_blocker_code is None
    )

    return CampaignSenderEligibility(
        account_id=int(account.id),
        platform=platform,
        display_identity=display,
        enabled=enabled,
        runtime_status=runtime_status,
        runtime_status_label=label,
        auth_ready=auth_ready,
        worker_ready=worker_ready,
        dispatch_ready=bool(dispatch_ready and campaign_eligible),
        campaign_eligible=campaign_eligible,
        blocker_code=blocker_code,
        blocker_label=blocker_label,
        execution_ready=execution_ready,
        execution_blocker_code=execution_blocker_code,
        execution_blocker_label=execution_blocker_label,
        daily_remaining=daily_remaining,
        hourly_remaining=hourly_remaining,
        next_send_at=next_send_at,
        last_verified_at=getattr(runtime, "last_verified_at", None),
        assigned=assigned,
        details={
            "lifecycle_status": account.status.value
            if hasattr(account.status, "value")
            else str(account.status),
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "l18_runtime_status": l18_status,
        },
    )


def evaluate_campaign_sender_eligibility_batch(
    db: Session,
    accounts: list[Account],
    *,
    campaign: Campaign | None = None,
    capacity_by_account: dict[int, dict[str, Any]] | None = None,
    assigned_ids: set[int] | None = None,
) -> dict[int, CampaignSenderEligibility]:
    runtimes = compute_all_account_runtime_statuses(db, accounts)
    by_rt = {int(r.account_id): r for r in runtimes}
    assigned_ids = assigned_ids or set()
    capacity_by_account = capacity_by_account or {}
    out: dict[int, CampaignSenderEligibility] = {}
    for account in accounts:
        aid = int(account.id)
        cap = capacity_by_account.get(aid) or {}
        out[aid] = evaluate_campaign_sender_eligibility(
            db,
            account,
            campaign=campaign,
            runtime=by_rt.get(aid),
            capacity_block_code=cap.get("block_code"),
            daily_remaining=cap.get("daily_remaining"),
            hourly_remaining=cap.get("hourly_remaining"),
            next_send_at=cap.get("next_send_at"),
            assigned=aid in assigned_ids,
        )
    return out


def filter_auto_select_eligible(
    db: Session,
    accounts: list[Account],
    *,
    campaign: Campaign | None = None,
) -> list[Account]:
    """Automatic selection may only pick campaign_eligible=True accounts."""
    elig = evaluate_campaign_sender_eligibility_batch(db, accounts, campaign=campaign)
    return [a for a in accounts if elig[int(a.id)].campaign_eligible]


def start_blocker_for_assigned_senders(
    elig_rows: list[CampaignSenderEligibility],
) -> tuple[str, str] | None:
    ready = [r for r in elig_rows if r.campaign_eligible]
    if ready:
        return None
    if not elig_rows:
        return ("NO_READY_SENDERS", "No campaign-eligible senders are available.")
    codes = {r.blocker_code for r in elig_rows if r.blocker_code}
    if codes == {"LOGIN_REQUIRED"}:
        return ("ASSIGNED_SENDER_LOGIN_REQUIRED", "Assigned sender requires login.")
    if codes == {"MANUAL_REVIEW"}:
        return ("ASSIGNED_SENDER_MANUAL_REVIEW", "Assigned sender requires manual review.")
    if codes == {"SESSION_ERROR"}:
        return ("ASSIGNED_SENDER_SESSION_ERROR", "Assigned sender has a session error.")
    if codes == {"AUTHENTICATED_NO_WORKER"}:
        return (
            "ASSIGNED_SENDER_WORKER_UNAVAILABLE",
            "Assigned sender has no healthy worker coverage.",
        )
    if codes == {"CAPACITY_EXHAUSTED"}:
        return ("CAPACITY_UNAVAILABLE", "Sender capacity is exhausted.")
    if codes == {"CIRCUIT_BLOCKED"}:
        return ("CAPACITY_UNAVAILABLE", "Sender circuit is temporarily blocked.")
    return ("CAMPAIGN_NOT_READY", "Campaign senders are not currently eligible to start.")
