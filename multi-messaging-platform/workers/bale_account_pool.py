"""مدیریت استخر چند اکانتی بله (BALE_DELIVERY_MODE=user_account).

مشابه workers/rubika_account_pool.py — همان الگو، همان منطق.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, date
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from core_engine.models import Account, AccountStatus, BaleAccountPool, BaleSenderSchedule

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger("workers.bale_account_pool")

IRAN_TZ = ZoneInfo("Asia/Tehran")


def resolve_current_bale_window(db: Session) -> bool:
    """آیا الان در بازه زمانی مجاز ارسال بله هستیم؟"""
    current_hour = datetime.now(IRAN_TZ).hour
    schedules = (
        db.query(BaleSenderSchedule)
        .filter(BaleSenderSchedule.is_active.is_(True))
        .all()
    )
    if not schedules:
        return True
    for schedule in schedules:
        start, end = schedule.start_hour, schedule.end_hour
        if start == end:
            continue
        if start < end:
            if start <= current_hour < end:
                return True
        else:
            if current_hour >= start or current_hour < end:
                return True
    return False


class BaleAccountPoolManager:
    """انتخاب و مدیریت سلامت اکانت‌های بله."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_active_accounts(self) -> list[Account]:
        rows = (
            self.db.query(BaleAccountPool, Account)
            .join(Account, Account.id == BaleAccountPool.account_id)
            .filter(
                Account.status == AccountStatus.ACTIVE,
                BaleAccountPool.is_healthy.is_(True),
            )
            .all()
        )
        return [account for _, account in rows]

    def get_pool_entry(self, account_id: int) -> BaleAccountPool | None:
        return (
            self.db.query(BaleAccountPool)
            .filter(BaleAccountPool.account_id == account_id)
            .first()
        )

    async def get_available_account(
        self,
        redis,
        hourly_cap: int = 30,
    ) -> "Account | None":
        from datetime import datetime, date
        from workers.rate_limit import is_min_delay_active, hourly_send_count
        from workers.config import get_worker_settings

        settings = get_worker_settings()
        now = datetime.now()
        current_hour = now.hour

        # بررسی پنجره زمانی مجاز
        start_hour = settings.BALE_SEND_WINDOW_START_HOUR
        end_hour = settings.BALE_SEND_WINDOW_END_HOUR
        if not (start_hour <= current_hour < end_hour):
            return None

        pool_entries = (
            self.db.query(BaleAccountPool, Account)
            .join(Account, Account.id == BaleAccountPool.account_id)
            .filter(
                BaleAccountPool.is_healthy == True,
                Account.status == AccountStatus.ACTIVE,
            )
            .order_by(BaleAccountPool.sent_today.asc())
            .all()
        )

        today = date.today()
        for pool_entry, account in pool_entries:
            # ریست شمارنده روزانه اگر روز عوض شده
            if pool_entry.last_count_reset_date and pool_entry.last_count_reset_date.date() < today:
                pool_entry.sent_today = 0
                pool_entry.last_count_reset_date = datetime.now()
                self.db.commit()

            # بررسی warm-up
            effective_daily_cap = settings.BALE_DAILY_SEND_CAP
            if settings.BALE_WARMUP_ENABLED and pool_entry.warm_up_started_at:
                days_active = (datetime.now() - pool_entry.warm_up_started_at).days
                if days_active < settings.BALE_WARMUP_DAYS:
                    effective_daily_cap = settings.BALE_WARMUP_START_CAP
                else:
                    if not pool_entry.is_warmed_up:
                        pool_entry.is_warmed_up = True
                        self.db.commit()

            # بررسی سقف روزانه
            if pool_entry.sent_today >= effective_daily_cap:
                continue

            # بررسی سقف ساعتی
            hourly_count = await hourly_send_count(redis, account.id)
            if hourly_count >= hourly_cap:
                continue

            # بررسی cooldown بین پیام‌ها
            if await is_min_delay_active(redis, account.id):
                continue

            return account

        return None

    def mark_account_used(self, *, account_id: int) -> None:
        pool_entry = self.get_pool_entry(account_id)
        if pool_entry:
            pool_entry.sent_today += 1
            self.db.flush()

    def mark_account_failed(
        self, *, account_id: int, error_message: str, permanent: bool = False
    ) -> None:
        pool_entry = self.get_pool_entry(account_id)
        if pool_entry:
            pool_entry.is_healthy = False
            pool_entry.last_error_at = datetime.utcnow()
            pool_entry.last_error_message = error_message[:500]
            self.db.flush()
        if permanent:
            account = (
                self.db.query(Account)
                .filter(Account.id == account_id)
                .first()
            )
            if account:
                account.status = AccountStatus.BANNED
                self.db.flush()
