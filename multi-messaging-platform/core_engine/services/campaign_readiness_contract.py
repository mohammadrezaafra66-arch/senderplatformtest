"""E2E campaign readiness contract — single counter/eligibility aggregation layer.

Preflight, Accounts API, sender picker, auto-select, and start gates must derive
operational readiness from ``evaluate_campaign_sender_eligibility`` (C1) consuming
L18 ``compute_account_runtime_status``.

Lifecycle ``Account.status == ACTIVE`` (enabled) is never operational readiness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from core_engine.models import Account, Campaign, PlatformType
from core_engine.services.campaign_sender_eligibility import (
    CampaignSenderEligibility,
    evaluate_campaign_sender_eligibility_batch,
)


@dataclass(slots=True)
class CampaignSenderCandidateAuditRow:
    account_id: int
    platform: str
    display_identity: str
    account_enabled: bool
    runtime_status: str
    runtime_status_label: str
    campaign_eligible: bool
    blocker_code: str | None
    blocker_label: str | None
    auth_ready: bool
    worker_ready: bool
    dispatch_ready: bool
    assigned: bool
    expected_ui_label: str
    expected_selectable: bool


@dataclass(slots=True)
class CampaignSenderCandidateAudit:
    total_candidates: int
    campaign_eligible_count: int
    blocked_count: int
    rows: list[CampaignSenderCandidateAuditRow] = field(default_factory=list)
    status_groups: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_candidates": self.total_candidates,
            "campaign_eligible_count": self.campaign_eligible_count,
            "blocked_count": self.blocked_count,
            "status_groups": self.status_groups,
            "rows": [
                {
                    "ACCOUNT_ID": r.account_id,
                    "PLATFORM": r.platform,
                    "DISPLAY_IDENTITY": r.display_identity,
                    "ACCOUNT_ENABLED": r.account_enabled,
                    "RUNTIME_STATUS": r.runtime_status,
                    "RUNTIME_LABEL": r.runtime_status_label,
                    "CAMPAIGN_ELIGIBLE": r.campaign_eligible,
                    "BLOCKER_CODE": r.blocker_code,
                    "BLOCKER_LABEL": r.blocker_label,
                    "EXPECTED_UI_LABEL": r.expected_ui_label,
                    "EXPECTED_SELECTABLE": r.expected_selectable,
                    "ASSIGNED": r.assigned,
                }
                for r in self.rows
            ],
        }


def lifecycle_active_accounts(
    db: Session,
    *,
    platform: PlatformType,
) -> list[Account]:
    """Accounts visible in campaign sender pool (lifecycle-active only)."""
    from core_engine.models import AccountStatus

    return (
        db.query(Account)
        .filter(
            Account.platform == platform,
            Account.status == AccountStatus.ACTIVE,
        )
        .order_by(Account.id.asc())
        .all()
    )


def audit_campaign_sender_candidates(
    db: Session,
    *,
    platform: PlatformType,
    campaign: Campaign | None = None,
    assigned_ids: set[int] | None = None,
    capacity_by_account: dict[int, dict[str, Any]] | None = None,
) -> CampaignSenderCandidateAudit:
    """Exhaustive sender candidate audit for one platform."""
    assigned_ids = assigned_ids or set()
    accounts = lifecycle_active_accounts(db, platform=platform)
    elig_by_id = evaluate_campaign_sender_eligibility_batch(
        db,
        accounts,
        campaign=campaign,
        assigned_ids=assigned_ids,
        capacity_by_account=capacity_by_account,
    )
    rows: list[CampaignSenderCandidateAuditRow] = []
    groups: dict[str, int] = {}
    eligible = 0
    for account in accounts:
        aid = int(account.id)
        e: CampaignSenderEligibility = elig_by_id[aid]
        groups[e.runtime_status] = groups.get(e.runtime_status, 0) + 1
        if e.campaign_eligible:
            eligible += 1
        rows.append(
            CampaignSenderCandidateAuditRow(
                account_id=aid,
                platform=e.platform,
                display_identity=e.display_identity,
                account_enabled=e.enabled,
                runtime_status=e.runtime_status,
                runtime_status_label=e.runtime_status_label,
                campaign_eligible=e.campaign_eligible,
                blocker_code=e.blocker_code,
                blocker_label=e.blocker_label,
                auth_ready=e.auth_ready,
                worker_ready=e.worker_ready,
                dispatch_ready=e.dispatch_ready,
                assigned=aid in assigned_ids,
                expected_ui_label=e.runtime_status_label,
                expected_selectable=e.campaign_eligible,
            )
        )
    return CampaignSenderCandidateAudit(
        total_candidates=len(rows),
        campaign_eligible_count=eligible,
        blocked_count=len(rows) - eligible,
        rows=rows,
        status_groups=groups,
    )


def count_campaign_eligible(
    elig_rows: list[CampaignSenderEligibility] | dict[int, CampaignSenderEligibility],
) -> int:
    if isinstance(elig_rows, dict):
        items = elig_rows.values()
    else:
        items = elig_rows
    return sum(1 for e in items if e.campaign_eligible)


def assignment_eligibility_warnings(
    elig_rows: list[CampaignSenderEligibility],
) -> list[dict[str, Any]]:
    """Non-blocking warnings when assigned senders are not campaign-eligible."""
    out: list[dict[str, Any]] = []
    for row in elig_rows:
        if not row.campaign_eligible:
            out.append(
                {
                    "account_id": row.account_id,
                    "code": row.blocker_code or "NOT_CAMPAIGN_ELIGIBLE",
                    "message": row.blocker_label or row.runtime_status_label,
                    "runtime_status": row.runtime_status,
                }
            )
    return out
