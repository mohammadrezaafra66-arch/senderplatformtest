"""مدیریت استخر چند اکانتی بله (BALE_DELIVERY_MODE=user_account).

همان معماری rubika_account_pool — دو منبع حقیقت موجود را دوباره پیاده نمی‌کند:
- سلامت اکانت: Account.status (ACTIVE/RESTING/BANNED)
- سقف ساعتی و min-delay: workers/rate_limit.py (Redis)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from core_engine.models import Account, AccountStatus, BaleAccountPool, BaleSenderSchedule

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger("workers.bale_account_pool")

IRAN_TZ = ZoneInfo("Asia/Tehran")


def resolve_current_phase(db: Session) -> str | None:
    """فاز فعال همین لحظه (به وقت ایران) را از bale_sender_schedules بخوان.

    اگر هیچ بازه فعالی ساعت جاری را پوشش ندهد، None برمی‌گرداند.
    """
    current_hour = datetime.now(IRAN_TZ).hour

    schedules = (
        db.query(BaleSenderSchedule)
        .filter(BaleSenderSchedule.is_active.is_(True))
        .order_by(BaleSenderSchedule.id.asc())
        .all()
    )
    for schedule in schedules:
        start, end = schedule.start_hour, schedule.end_hour
        if start == end:
            continue
        if start < end:
            if start <= current_hour < end:
                return schedule.phase
        else:
            if current_hour >= start or current_hour < end:
                return schedule.phase
    return None


class BaleAccountPoolManager:
    """انتخاب و مدیریت سلامت اکانت‌های بله برای یک فاز مشخص."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_pool_accounts(self, phase: str) -> list[Account]:
        """اکانت‌های سالم (Account.status==ACTIVE) این فاز، به ترتیب priority."""
        rows = (
            self.db.query(BaleAccountPool, Account)
            .join(Account, Account.id == BaleAccountPool.account_id)
            .filter(
                BaleAccountPool.phase == phase,
                Account.status == AccountStatus.ACTIVE,
            )
            .order_by(
                BaleAccountPool.priority.asc(),
                Account.last_used_at.asc().nulls_first(),
            )
            .all()
        )
        return [account for _pool_row, account in rows]

    async def get_available_account(
        self,
        *,
        phase: str,
        redis: "Redis",
        hourly_cap: int,
    ) -> Account | None:
        """اولین اکانت سالم این فاز که در cooldown یا سقف ساعتی نیست."""
        from workers.rate_limit import is_hourly_cap_reached, is_min_delay_active

        for account in self.list_pool_accounts(phase):
            if await is_min_delay_active(redis, account.id):
                continue
            if await is_hourly_cap_reached(redis, account.id, hourly_cap):
                continue
            return account
        return None

    def mark_account_used(self, *, account_id: int) -> None:
        account = self.db.query(Account).filter(Account.id == account_id).first()
        if account is not None:
            account.last_used_at = datetime.utcnow()
            self.db.flush()

    def mark_account_failed(
        self,
        *,
        account_id: int,
        error_message: str,
        permanent: bool = False,
    ) -> None:
        account = self.db.query(Account).filter(Account.id == account_id).first()
        if account is None:
            logger.warning("bale_pool_mark_failed_account_missing account_id=%s", account_id)
            return

        account.status = AccountStatus.BANNED if permanent else AccountStatus.RESTING

        now = datetime.utcnow()
        pool_rows = (
            self.db.query(BaleAccountPool)
            .filter(BaleAccountPool.account_id == account_id)
            .all()
        )
        for row in pool_rows:
            row.last_error_at = now
            row.last_error_message = error_message[:512]

        self.db.flush()
        logger.error(
            "bale_pool_account_marked_failed account_id=%s permanent=%s error=%s",
            account_id,
            permanent,
            error_message,
        )

    def mark_account_restored(self, *, account_id: int) -> None:
        """RESTING → ACTIVE. BANNED را تغییر نمی‌دهد."""
        account = self.db.query(Account).filter(Account.id == account_id).first()
        if account is not None and account.status == AccountStatus.RESTING:
            account.status = AccountStatus.ACTIVE
            self.db.flush()
            logger.info("bale_pool_account_restored account_id=%s", account_id)
