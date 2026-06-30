"""مدیریت استخر چند اکانتی روبیکا (RUBIKA_DELIVERY_MODE=user_account).

طراحی عمداً سبک — دو منبع حقیقت موجود را دوباره پیاده نمی‌کند:
- سلامت اکانت: core_engine.models.Account.status (ACTIVE/RESTING/BANNED) —
  دقیقاً همان فیلدی که evaluate_account_session_readiness و کل سیستم استفاده می‌کنند.
- سقف ساعتی و min-delay: workers/rate_limit.py (Redis) — همان مکانیزم WhatsApp
  (WHATSAPP_HOURLY_SEND_CAP / WHATSAPP_MIN_SEND_DELAY_SECONDS).

این ماژول فقط مسئول «کدام اکانت این فاز را بفرستیم» و «این اکانت خراب شد، کنار بگذار» است.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from core_engine.models import Account, AccountStatus, RubikaAccountPool, RubikaSenderSchedule

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger("workers.rubika_account_pool")

IRAN_TZ = ZoneInfo("Asia/Tehran")


def resolve_current_phase(db: Session) -> str | None:
    """فاز فعال همین لحظه (به وقت ایران) را از rubika_sender_schedules بخوان.

    عمداً از DB می‌خواند نه از WorkerSettings — چون WorkerSettings در فرایند
    Celery کش می‌شود (lru_cache) و تغییر از پنل ادمین (فاز ۴) بدون ری‌استارت
    اعمال نمی‌شد. RUBIKA_DAY_PHASE_START/END_HOUR در workers/config.py فقط مقدار
    اولیه‌ای است که migration فاز ۱ در همین جدول seed کرده.

    اگر هیچ بازه فعالی ساعت جاری را پوشش ندهد، None برمی‌گرداند — یعنی «خارج از
    بازه ارسال، صبر کن» (نیازمندی ۶ سند).
    """
    current_hour = datetime.now(IRAN_TZ).hour

    schedules = (
        db.query(RubikaSenderSchedule)
        .filter(RubikaSenderSchedule.is_active.is_(True))
        .order_by(RubikaSenderSchedule.id.asc())
        .all()
    )
    for schedule in schedules:
        start, end = schedule.start_hour, schedule.end_hour
        if start == end:
            continue  # بازه صفر — نادیده بگیر
        if start < end:
            if start <= current_hour < end:
                return schedule.phase
        else:
            # wraparound — مثلاً شب: ۲۲ تا ۸
            if current_hour >= start or current_hour < end:
                return schedule.phase
    return None



class RubikaAccountPoolManager:
    """انتخاب و مدیریت سلامت اکانت‌های روبیکا برای یک فاز مشخص (day/night/listener/status)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_pool_accounts(self, phase: str) -> list[Account]:
        """اکانت‌های سالم (Account.status==ACTIVE) این فاز، به ترتیب priority."""
        rows = (
            self.db.query(RubikaAccountPool, Account)
            .join(Account, Account.id == RubikaAccountPool.account_id)
            .filter(
                RubikaAccountPool.phase == phase,
                Account.status == AccountStatus.ACTIVE,
            )
            .order_by(
                RubikaAccountPool.priority.asc(),
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
        """اولین اکانت سالم این فاز که در cooldown یا سقف ساعتی نیست (round-robin طبیعی

        چون هر بار که یک اکانت استفاده می‌شود last_used_at آن به‌روز می‌شود و در صف
        ordering به انتها می‌رود).
        """
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
        """اکانت را ناسالم کن. Account.status منبع حقیقت سلامت در کل سیستم است —

        اینجا یک فلگ جدا (مثل is_healthy) ساخته نمی‌شود تا با evaluate_account_session_readiness
        و بقیه سیستم ناهمگام نشود. last_error_at/last_error_message در rubika_account_pool
        فقط زمینه (context) برای نوتیف/بازبینی است.
        """
        account = self.db.query(Account).filter(Account.id == account_id).first()
        if account is None:
            logger.warning("rubika_pool_mark_failed_account_missing account_id=%s", account_id)
            return

        account.status = AccountStatus.BANNED if permanent else AccountStatus.RESTING

        now = datetime.utcnow()
        pool_rows = (
            self.db.query(RubikaAccountPool)
            .filter(RubikaAccountPool.account_id == account_id)
            .all()
        )
        for row in pool_rows:
            row.last_error_at = now
            row.last_error_message = error_message[:512]

        self.db.flush()
        logger.error(
            "rubika_pool_account_marked_failed account_id=%s permanent=%s error=%s",
            account_id,
            permanent,
            error_message,
        )
        # TODO(فاز ۴): اتصال این رویداد به یک کانال نوتیف واقعی برای ادمین (مثلاً پیام
        # داخلی یا ایمیل). فعلاً به‌صورت ساخت‌یافته لاگ می‌شود (last_error_at/message در
        # rubika_account_pool ذخیره است) و از طریق GET /rubika/accounts قابل خواندن خواهد بود.

    def mark_account_restored(self, *, account_id: int) -> None:
        """RESTING → ACTIVE (بازبینی دستی تأیید کرد اکانت سالم است). BANNED را تغییر نمی‌دهد."""
        account = self.db.query(Account).filter(Account.id == account_id).first()
        if account is not None and account.status == AccountStatus.RESTING:
            account.status = AccountStatus.ACTIVE
            self.db.flush()
            logger.info("rubika_pool_account_restored account_id=%s", account_id)
