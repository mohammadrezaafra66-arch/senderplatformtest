"""ãÍÇÓÈå ÓÞÝ ÑæÒÇäå á˜Çäí ÈÑ ÇÓÇÓ ÑæÒåÇí warm-up."""

from datetime import datetime
from sqlalchemy.orm import Session
from core_engine.models import TelegramAccountPool
from core_engine.config import get_settings


def calculate_daily_cap(days_since_start: int) -> int:
    settings = get_settings()
    warmup_days = getattr(settings, "TELEGRAM_WARMUP_DAYS", 14)
    start_cap = getattr(settings, "TELEGRAM_WARMUP_START_CAP", 10)
    final_cap = getattr(settings, "TELEGRAM_WARMUP_FINAL_CAP", 80)

    if days_since_start >= warmup_days:
        return final_cap

    step = (final_cap - start_cap) / warmup_days
    return int(start_cap + step * days_since_start)


def refresh_all_account_caps(db: Session) -> dict:
    accounts = db.query(TelegramAccountPool).all()
    updated = 0

    for acc in accounts:
        if acc.warm_up_started_at:
            days_passed = (datetime.utcnow() - acc.warm_up_started_at).days
            acc.daily_cap_today = calculate_daily_cap(days_passed)
            warmup_days = getattr(get_settings(), "TELEGRAM_WARMUP_DAYS", 14)
            if days_passed >= warmup_days:
                acc.is_warmed_up = True

        acc.sent_today = 0
        acc.last_count_reset_date = datetime.utcnow()
        updated += 1

    db.commit()
    return {"accounts_updated": updated}
