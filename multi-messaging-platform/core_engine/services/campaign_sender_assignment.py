"""Resolve the deterministic sender pool for campaign message preparation."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
)


def _sender_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"code": code, "message": message},
    )


def resolve_campaign_sender_accounts(db: Session, campaign: Campaign) -> list[Account]:
    """Return one validated sender pool; an empty relation set means Auto mode."""
    links = (
        db.query(CampaignAccount)
        .filter(CampaignAccount.campaign_id == campaign.id)
        .order_by(CampaignAccount.priority.asc(), CampaignAccount.id.asc())
        .all()
    )
    if not links:
        accounts = (
            db.query(Account)
            .filter(
                Account.status == AccountStatus.ACTIVE,
                Account.platform == campaign.platform,
            )
            .order_by(Account.id.asc())
            .all()
        )
        if not accounts:
            raise _sender_error(
                "no_active_sender_account",
                f"No active sender account is available for {campaign.platform.value}.",
            )
        return accounts

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
        result.append(account)
    return result
