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
        redis: "Redis",
        hourly_cap: int,
    ) -> Account | None:
        from workers.rate_limit import is_in_cooldown, get_hourly_count

        accounts = self.list_active_accounts()
        if not accounts:
            return None
        random.shuffle(accounts)
        for account in accounts:
            pool_entry = self.get_pool_entry(account.id)
            if pool_entry is None:
                continue
            today = date.today()
            last_reset = pool_entry.last_count_reset_date
            if isinstance(last_reset, datetime):
                last_reset = last_reset.date()
            if last_reset < today:
                pool_entry.sent_today = 0
                pool_entry.last_count_reset_date = datetime.utcnow()
                self.db.flush()
            if pool_entry.sent_today >= pool_entry.daily_cap_today:
                continue
            if await is_in_cooldown(redis, account.id):
                continue
            hourly_count = await get_hourly_count(redis, account.id)
            if hourly_count >= hourly_cap:
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
