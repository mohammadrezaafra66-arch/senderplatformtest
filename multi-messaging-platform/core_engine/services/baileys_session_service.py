"""Update channel_sessions / account status when Baileys session is invalid."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from core_engine.models import Account, AccountStatus, PlatformType
from core_engine.services.whatsapp_web_session import store_whatsapp_web_session

logger = logging.getLogger(__name__)


def _digits(value: str | int | None) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def resolve_account_by_phone(db: Session, phone_digits: str) -> Account | None:
    """Find WhatsApp account by E.164 digits (Baileys accountId)."""
    key = _digits(phone_digits)
    if not key:
        return None

    matches = [
        account
        for account in db.query(Account).filter(Account.platform == PlatformType.WHATSAPP).all()
        if _digits(account.phone_number) == key
    ]
    if not matches:
        return None
    return max(matches, key=lambda row: row.id)


def mark_baileys_session_disconnected(
    db: Session,
    phone_digits: str,
    *,
    reason: str = "session_invalid",
) -> bool:
    """Mark channel session unlinked and account requires re-login."""
    account = resolve_account_by_phone(db, phone_digits)
    if account is None:
        logger.warning("baileys disconnect: no account for phone suffix=%s", phone_digits[-4:])
        return False

    profile_dir = f"sessions/{_digits(phone_digits)}"
    store_whatsapp_web_session(
        db,
        account_id=account.id,
        linked=False,
        phone=_digits(phone_digits),
        profile_dir=profile_dir,
    )

    account.status = AccountStatus.REQUIRES_LOGIN

    db.flush()
    logger.info(
        "baileys session marked disconnected account_id=%s phone_suffix=%s reason=%s",
        account.id,
        phone_digits[-4:] if len(phone_digits) >= 4 else "****",
        reason,
    )
    return True
