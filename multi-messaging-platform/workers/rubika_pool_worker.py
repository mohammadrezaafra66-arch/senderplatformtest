"""Rubika multi-account pool worker — dynamic eligible accounts, fair queues."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from core_engine.database import SessionLocal
from core_engine.models import Account, AccountStatus, PlatformType, RubikaAccountPool

from workers.config import WorkerSettings, get_worker_settings
from workers.delivery import deliver_platform_message
from workers.multi_account_worker import MultiAccountWorker
from workers.payloads import WorkerPayload, WorkerResult
from workers.pool_health import publish_account_coverage, publish_worker_heartbeat, resolve_worker_hostname
from workers.rubika_account_pool import resolve_current_phase


def discover_rubika_eligible_account_ids(
    *,
    explicit_ids: list[int] | None = None,
    clock: datetime | None = None,
) -> list[int]:
    """Discover ACTIVE Rubika accounts eligible for the current send phase.

    Prefer explicit env list when provided; otherwise load pool membership for
    the current ``resolve_current_phase`` window. Supports 43+ accounts.
    """
    if explicit_ids:
        return sorted({int(x) for x in explicit_ids})

    db = SessionLocal()
    try:
        phase = resolve_current_phase(db, clock=clock)
        if phase is None:
            return []
        rows = (
            db.query(RubikaAccountPool.account_id)
            .join(Account, Account.id == RubikaAccountPool.account_id)
            .filter(
                RubikaAccountPool.phase == phase,
                Account.platform == PlatformType.RUBIKA,
                Account.status == AccountStatus.ACTIVE,
            )
            .order_by(RubikaAccountPool.priority.asc(), RubikaAccountPool.account_id.asc())
            .all()
        )
        return [int(r[0]) for r in rows]
    finally:
        db.close()


class RubikaPoolWorker(MultiAccountWorker):
    """Poll ``queue:rubika:{account_id}`` for many accounts in one process."""

    def __init__(
        self,
        *,
        account_ids: list[int],
        redis_url: str,
        database_url: str,
        poll_interval_seconds: int = 5,
        log_level: str = "INFO",
        settings: WorkerSettings | None = None,
        max_retry_attempts: int = 3,
        retry_base_delay_seconds: float = 5.0,
        execution_enabled: bool = True,
        account_refresh_interval_seconds: int = 60,
        heartbeat_interval_seconds: int = 15,
        heartbeat_ttl_seconds: int = 45,
    ) -> None:
        super().__init__(
            platform="rubika",
            account_ids=account_ids,
            redis_url=redis_url,
            database_url=database_url,
            poll_interval_seconds=poll_interval_seconds,
            log_level=log_level,
            max_retry_attempts=max_retry_attempts,
            retry_base_delay_seconds=retry_base_delay_seconds,
            execution_enabled=execution_enabled,
        )
        self._settings = settings
        self._account_refresh_interval_seconds = max(0, int(account_refresh_interval_seconds))
        self._heartbeat_interval_seconds = max(1, int(heartbeat_interval_seconds))
        self._heartbeat_ttl_seconds = max(self._heartbeat_interval_seconds + 5, int(heartbeat_ttl_seconds))
        self._hostname = resolve_worker_hostname()
        self._heartbeat_task: asyncio.Task | None = None
        self._account_refresh_task: asyncio.Task | None = None
        self._explicit_account_ids = list(account_ids) if account_ids else None

    def _get_settings(self) -> WorkerSettings:
        return self._settings or get_worker_settings()

    def replace_account_ids(self, account_ids: list[int]) -> None:
        self.account_ids = sorted({int(a) for a in account_ids})
        self._allowed_account_ids = {str(a) for a in self.account_ids}
        if self.account_ids:
            self._round_robin_index %= len(self.account_ids)
        else:
            self._round_robin_index = 0

    async def _refresh_accounts_loop(self) -> None:
        while True:
            await asyncio.sleep(self._account_refresh_interval_seconds or 60)
            try:
                ids = discover_rubika_eligible_account_ids(
                    explicit_ids=self._explicit_account_ids
                )
                if ids != self.account_ids:
                    self.replace_account_ids(ids)
                    self.logger.info(
                        "rubika_pool_accounts_refreshed count=%s ids=%s",
                        len(ids),
                        ",".join(str(i) for i in ids),
                    )
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("rubika_pool_refresh_failed error=%s", type(exc).__name__)

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                if self._redis is not None and self.account_ids:
                    await publish_worker_heartbeat(
                        self.redis,
                        platform="rubika",
                        hostname=self._hostname,
                        assigned_account_ids=self.account_ids,
                        pool_size=1,
                        pool_index=0,
                        ttl_seconds=self._heartbeat_ttl_seconds,
                    )
                    await publish_account_coverage(
                        self.redis,
                        platform="rubika",
                        account_ids=self.account_ids,
                        hostname=self._hostname,
                        ttl_seconds=self._heartbeat_ttl_seconds,
                    )
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("rubika_pool_heartbeat_failed error=%s", type(exc).__name__)
            await asyncio.sleep(self._heartbeat_interval_seconds)

    async def send_message(self, payload: WorkerPayload) -> WorkerResult:
        # Exact account routing: payload.account_id already validated against allow-list.
        return await deliver_platform_message(
            self.platform,
            payload,
            self._get_settings(),
        )

    async def run_forever(self) -> None:
        if not self.execution_enabled:
            from workers.base_worker import WorkerExecutionDisabled

            raise WorkerExecutionDisabled(
                "WORKER_EXECUTION_ENABLED=false — refusing Rubika pool startup."
            )
        if not self.account_ids:
            discovered = discover_rubika_eligible_account_ids(
                explicit_ids=self._explicit_account_ids
            )
            self.replace_account_ids(discovered)
        await self.connect()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        if self._account_refresh_interval_seconds > 0:
            self._account_refresh_task = asyncio.create_task(self._refresh_accounts_loop())
        try:
            while True:
                await self.run_once()
                await asyncio.sleep(self.poll_interval_seconds)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            for task in (self._heartbeat_task, self._account_refresh_task):
                if task is not None:
                    task.cancel()
            await self.disconnect()
