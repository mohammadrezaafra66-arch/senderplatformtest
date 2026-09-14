"""Campaign message preparation readiness — inputs required before prepare runs."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core_engine.models import Campaign, CampaignStatus
from core_engine.services.campaign_sender_assignment import resolve_campaign_sender_accounts
from core_engine.services.product_feed.errors import PRODUCT_FEED_ERROR_CODES

TEMPLATE_MISSING = "TEMPLATE_MISSING"
NO_RECIPIENTS = "NO_RECIPIENTS"
NO_SENDERS = "NO_SENDERS"

_PERSIAN: dict[str, str] = {
    TEMPLATE_MISSING: "متن پیام مشخص نشده است.",
    NO_RECIPIENTS: "گیرنده‌ای برای کمپین ثبت نشده است.",
    NO_SENDERS: "فرستنده‌ای برای کمپین تخصیص داده نشده است.",
    "PRODUCT_FEED_EMPTY": "هیچ محصول تبلیغاتی معتبری یافت نشد.",
    "INSUFFICIENT_ADVERTISING_PRODUCTS": (
        "محصولات معتبر کافی برای ساخت پیام وجود ندارد."
    ),
    "PRODUCT_FEED_STALE": "قیمت محصولات قدیمی است؛ لطفاً چند دقیقه دیگر دوباره تلاش کنید.",
    "PRODUCT_FEED_UNAVAILABLE": "دریافت اطلاعات محصولات ناموفق بود.",
    "GPT_UNAVAILABLE": "سرویس تولید متن پیام در دسترس نیست.",
    "GPT_RENDER_FAILED": "تولید متن پیام ناموفق بود.",
}


def _issue(code: str, message: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message or _PERSIAN.get(code, code),
    }


def evaluate_preparation_readiness(db: Session, campaign: Campaign) -> list[dict[str, Any]]:
    """Return blockers that would prevent prepare from succeeding (read-only)."""
    from core_engine.models import CampaignRecipient, Contact
    from core_engine.services.phase4_utils import is_consent_allowed

    blockers: list[dict[str, Any]] = []

    template = (campaign.template_text or "").strip()
    if not template:
        blockers.append(_issue(TEMPLATE_MISSING))
        return blockers

    recipient_rows = (
        db.query(CampaignRecipient, Contact)
        .join(Contact, CampaignRecipient.contact_id == Contact.id)
        .filter(CampaignRecipient.campaign_id == campaign.id)
        .all()
    )
    eligible = [
        contact
        for _recipient, contact in recipient_rows
        if is_consent_allowed(contact.consent_status)
    ]
    if not eligible:
        blockers.append(_issue(NO_RECIPIENTS))
        return blockers

    try:
        senders = resolve_campaign_sender_accounts(db, campaign)
        if not senders:
            blockers.append(_issue(NO_SENDERS))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = str(detail.get("code") or NO_SENDERS)
        message = detail.get("message")
        blockers.append(
            _issue(
                code,
                message if isinstance(message, str) and message.strip() else None,
            )
        )

    if campaign.include_products:
        from core_engine.services.product_feed.service import product_feed_status

        status = product_feed_status()
        if not status.get("ok"):
            code = str(status.get("code") or "PRODUCT_FEED_UNAVAILABLE")
            message = status.get("message")
            if isinstance(message, str) and message.strip():
                blockers.append(_issue(code, message))
            else:
                blockers.append(_issue(code))

    return blockers


def preparation_blocker_message(code: str, fallback: str | None = None) -> str:
    return _PERSIAN.get(code, fallback or code)


def is_product_feed_blocker(code: str) -> bool:
    return code in PRODUCT_FEED_ERROR_CODES
