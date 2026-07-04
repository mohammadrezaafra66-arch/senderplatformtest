"""WhatsApp worker pool — multiple account queues, one browser at a time."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone

from sqlalchemy.orm import Session, joinedload

from core_engine.database import SessionLocal
from core_engine.models import Account, AccountSendSettings
from core_engine.services.alert_service import send_alert
from core_engine.services.delivery_audit import record_worker_whatsapp_delivery

from workers.config import WorkerSettings, get_worker_settings
from workers.delivery import deliver_platform_message
from workers.distributed_lock import RedisDistributedLock
from workers.multi_account_worker import MultiAccountWorker
from workers.payloads import WorkerPayload, WorkerResult
from workers.pool_health import publish_worker_heartbeat, resolve_worker_hostname
from workers.rate_limit import (
    can_send_daily,
    incr_daily_count,
    is_hourly_cap_reached,
    is_min_delay_active,
    record_successful_send,
    set_min_delay,
)
from workers.redis_keys import daily_cap_alert_key, whatsapp_browser_lock_key

# TTL فلگ ضدِ-spam هشدار سقف روزانه — همان پنجره شمارنده روزانه (۴۸ ساعت).
_DAILY_CAP_ALERT_TTL_SECONDS = 172800


def _get_account_delay(account_id: int) -> tuple[int, int]:
    """خواندن min/max delay از DB. در صورت خطا، پیشفرض امن برگردانده می‌شود."""
    try:
        db: Session = SessionLocal()
        settings = (
            db.query(AccountSendSettings)
            .filter_by(account_id=account_id)
            .first()
        )
        db.close()
        if settings:
            return settings.min_delay_seconds, settings.max_delay_seconds
    except Exception:
        pass
    return 45, 90  # پیشفرض امن


def _load_account(account_id: int) -> Account | None:
    """اکانت را با policy (eager) بارگذاری می‌کند تا warming + daily cap محاسبه شود.

    policy را joinedload می‌کنیم تا پس از close نیازی به lazy-load نباشد.
    """
    try:
        db: Session = SessionLocal()
        try:
            return (
                db.query(Account)
                .options(joinedload(Account.policy))
                .filter(Account.id == account_id)
                .first()
            )
        finally:
            db.close()
    except Exception:
        return None


class WhatsAppPoolWorker(MultiAccountWorker):
    """Poll several `queue:whatsapp:{account_id}` keys in a single process.

    WA-5 safeguards:
    - process-wide browser lock (one Chromium at a time per replica)
    - Redis distributed lock per account (safe horizontal scaling)
    - per-account min delay + hourly cap
    - retry with exponential backoff via MultiAccountWorker
    - Redis heartbeat for self-healing observability
    """

    def __init__(
        self,
        *,
        account_ids: list[int],
        redis_url: str,
        database_url: str,
        poll_interval_seconds: int = 5,
        log_level: str = "INFO",
        browser_lock_enabled: bool = True,
        settings: WorkerSettings | None = None,
        max_retry_attempts: int = 3,
        retry_base_delay_seconds: float = 5.0,
        pool_size: int = 1,
        pool_index: int = 0,
    ) -> None:
        super().__init__(
            platform="whatsapp",
            account_ids=account_ids,
            redis_url=redis_url,
            database_url=database_url,
            poll_interval_seconds=poll_interval_seconds,
            log_level=log_level,
            max_retry_attempts=max_retry_attempts,
            retry_base_delay_seconds=retry_base_delay_seconds,
        )
        self._settings = settings
        self._browser_lock_enabled = browser_lock_enabled
        self._browser_lock = asyncio.Lock()
        self._pool_size = pool_size
        self._pool_index = pool_index
        self._hostname = resolve_worker_hostname()
        self._heartbeat_task: asyncio.Task | None = None

    def _get_settings(self) -> WorkerSettings:
        return self._settings or get_worker_settings()

    async def connect(self) -> None:
        await super().connect()
        if self._get_settings().WORKER_HEARTBEAT_INTERVAL_SECONDS > 0:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def disconnect(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
        await super().disconnect()

    async def _heartbeat_loop(self) -> None:
        settings = self._get_settings()
        interval = max(settings.WORKER_HEARTBEAT_INTERVAL_SECONDS, 1)
        while True:
            try:
                await publish_worker_heartbeat(
                    self.redis,
                    platform=self.platform,
                    hostname=self._hostname,
                    assigned_account_ids=self.account_ids,
                    pool_size=self._pool_size,
                    pool_index=self._pool_index,
                    ttl_seconds=settings.WORKER_HEARTBEAT_TTL_SECONDS,
                )
            except Exception:
                self.logger.exception("heartbeat_publish_failed")
            await asyncio.sleep(interval)

    async def send_message(self, payload: WorkerPayload) -> WorkerResult:
        settings = self._get_settings()
        account_id = payload.account_id

        if await is_min_delay_active(self.redis, account_id):
            return WorkerResult(
                success=False,
                status="failed_retryable",
                error_code="whatsapp_send_throttled",
                error_message="Minimum delay between WhatsApp sends is active.",
                retryable=True,
            )

        if await is_hourly_cap_reached(
            self.redis,
            account_id,
            settings.WHATSAPP_HOURLY_SEND_CAP,
        ):
            return WorkerResult(
                success=False,
                status="failed_retryable",
                error_code="whatsapp_hourly_cap_reached",
                error_message="Hourly WhatsApp send cap reached for this account.",
                retryable=True,
            )

        # Phase 4 — سقف روزانه + warming ramp. رفتار hourly دست‌نخورده می‌ماند.
        account = _load_account(account_id)
        if account is not None:
            ok, reason, count, cap = await can_send_daily(account, self.redis)
            if not ok:
                await self._alert_daily_cap_once(account_id, count, cap)
                return WorkerResult(
                    success=False,
                    status="failed_retryable",
                    error_code="whatsapp_daily_cap_reached",
                    error_message=reason
                    or "Daily WhatsApp send cap reached for this account.",
                    retryable=True,
                )

        distributed_lock: RedisDistributedLock | None = None
        if settings.WHATSAPP_DISTRIBUTED_LOCK_ENABLED:
            distributed_lock = RedisDistributedLock(
                self.redis,
                whatsapp_browser_lock_key(account_id),
                ttl_seconds=settings.WHATSAPP_DISTRIBUTED_LOCK_TTL_SECONDS,
            )
            if not await distributed_lock.acquire():
                return WorkerResult(
                    success=False,
                    status="failed_retryable",
                    error_code="whatsapp_browser_lock_busy",
                    error_message="Another worker replica holds the browser lock.",
                    retryable=True,
                )

        try:
            if self._browser_lock_enabled:
                async with self._browser_lock:
                    result = await deliver_platform_message(
                        self.platform,
                        payload,
                        settings,
                    )
            else:
                result = await deliver_platform_message(
                    self.platform,
                    payload,
                    settings,
                )
        finally:
            if distributed_lock is not None:
                await distributed_lock.release()

        if result.success:
            await record_successful_send(self.redis, account_id)
            await incr_daily_count(self.redis, account_id)
            min_d, max_d = _get_account_delay(account_id)
            actual_delay = random.uniform(min_d, max_d)
            await set_min_delay(
                self.redis,
                account_id,
                int(actual_delay),
            )

        return result

    async def _alert_daily_cap_once(
        self,
        account_id: int,
        count: int,
        cap: int,
    ) -> None:
        """هشدار سقف روزانه — فقط بار اولِ رسیدن به سقف در روز (transition-guard).

        فلگ Redis با SET NX اتمیک است؛ پس در چند replica هم فقط یک‌بار alert می‌رود.
        """
        try:
            day = datetime.now(timezone.utc).strftime("%Y%m%d")
            first = await self.redis.set(
                daily_cap_alert_key(account_id, day),
                "1",
                nx=True,
                ex=_DAILY_CAP_ALERT_TTL_SECONDS,
            )
            if first:
                await send_alert("daily_cap_reached", account_id, f"{count}/{cap}")
        except Exception:
            self.logger.exception("daily_cap_alert_failed")

    async def handle_result(
        self,
        payload: WorkerPayload,
        result: WorkerResult,
        *,
        raw_payload: str | None = None,
        account_id: int | str | None = None,
    ) -> None:
        record_worker_whatsapp_delivery(payload, result)
        await super().handle_result(
            payload,
            result,
            raw_payload=raw_payload,
            account_id=account_id,
        )

    async def run_forever(self) -> None:
        await self.connect()
        try:
            while True:
                await self.run_once()
                await asyncio.sleep(self.poll_interval_seconds)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await self.disconnect()
