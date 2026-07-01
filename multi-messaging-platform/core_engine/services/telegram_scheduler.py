"""ÈÑÑÓí ÈÇÒå ÒãÇäí ãÌÇÒ ÇÑÓÇá MTProto."""

from datetime import datetime
from sqlalchemy.orm import Session
from core_engine.models import TelegramSenderSchedule, Campaign


async def check_and_enforce_telegram_send_window(db: Session) -> dict:
    schedule = db.query(TelegramSenderSchedule).filter(
        TelegramSenderSchedule.is_active == True
    ).first()

    if not schedule:
        return {"status": "no_schedule_configured"}

    current_hour = datetime.now().hour
    within_window = schedule.start_hour <= current_hour < schedule.end_hour

    campaigns = db.query(Campaign).filter(Campaign.platform == "telegram").all()
    affected = 0

    for campaign in campaigns:
        if not within_window and campaign.status == "running":
            campaign.status = "paused"
            affected += 1
        elif within_window and campaign.status == "paused":
            campaign.status = "running"
            affected += 1

    db.commit()
    return {"within_window": within_window, "campaigns_affected": affected}
