"""Rubika multi-account pool worker — controlled discovery + fair queues."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from core_engine.database import SessionLocal

from workers.config import WorkerSettings, get_worker_settings
from workers.delivery import deliver_platform_message
from workers.multi_account_worker import MultiAccountWorker
from workers.payloads import WorkerPayload, WorkerResult
from workers.pool_health import publish_account_coverage, publish_worker_heartbeat, resolve_worker_hostname
from workers.account_pool import parse_account_id_list
from workers.rubika_worker_discovery import (
    MODE_DYNAMIC,
    MODE_PINNED,
    MODE_SHADOW,
    get_dispatch_eligible_rubika_account_ids,
    normalize_discovery_mode,
    reconcile_worker_account_ids,
    resolve_actual_worker_account_ids,
)


def discover_rubika_eligible_account_ids(
    *,
    explicit_ids: list[int] | None = None,
    clock: datetime | None = None,
    db=None,
) -> list[int]:
    """Backward-compatible wrapper.

    Prefer ``get_dispatch_eligible_rubika_account_ids`` for authoritative eligibility.
    When ``explicit_ids`` is provided (legacy pin path), returns that sorted set.
    """
    if explicit_ids:
        return sorted({int(x) for x in explicit_ids})

    owns_db = db is None
    session = db if db is not None else SessionLocal()
    try:
        return get_dispatch_eligible_rubika_account_ids(session, clock=clock)
    finally:
        if owns_db:
            session.close()


def resolve_rubika_worker_account_ids_from_settings(
    settings: WorkerSettings,
    *,
    clock: datetime | None = None,
    db=None,
) -> tuple[list[int], dict]:
    """Resolve actual coverage IDs + safe discovery metadata (no Redis writes)."""
    mode = normalize_discovery_mode(
        getattr(settings, "RUBIKA_WORKER_DISCOVERY_MODE", MODE_PINNED)
    )
    pinned = parse_account_id_list(getattr(settings, "RUBIKA_ACCOUNT_IDS", "") or "")
    cohort = parse_account_id_list(
        getattr(settings, "RUBIKA_WORKER_DISCOVERY_COHORT_IDS", "") or ""
    )
    discovery_scope = getattr(settings, "RUBIKA_WORKER_DISCOVERY_SCOPE", None)

    owns_db = db is None
    session = db if db is not None else SessionLocal()
    try:
        dynamic = get_dispatch_eligible_rubika_account_ids(
            session, clock=clock, cohort_ids=None
        )
        actual = resolve_actual_worker_account_ids(
            mode=mode,
            pinned_ids=pinned,
            dynamic_eligible_ids=dynamic,
            cohort_ids=cohort if mode == MODE_DYNAMIC else [],
            discovery_scope=discovery_scope,
        )
        meta = {
            "mode": mode,
            "pinned_ids": pinned,
            "dynamic_eligible_ids": dynamic,
            "actual_worker_ids": actual,
            "cohort_ids": cohort,
            "discovery_scope": discovery_scope,
            "would_add": sorted(set(dynamic) - set(pinned)),
            "would_remove": sorted(set(pinned) - set(dynamic)),
        }
        return actual, meta
    finally:
        if owns_db:
            session.close()


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
        discovery_mode: str | None = None,
        pinned_account_ids: list[int] | None = None,
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
        # Pin only when mode is pinned/shadow OR explicit pin list provided.
        cfg = settings
        mode = normalize_discovery_mode(
            discovery_mode
            if discovery_mode is not None
            else (getattr(cfg, "RUBIKA_WORKER_DISCOVERY_MODE", MODE_PINNED) if cfg else MODE_PINNED)
        )
        self._discovery_mode = mode
        if pinned_account_ids is not None:
            self._pinned_account_ids = sorted({int(x) for x in pinned_account_ids})
        elif cfg is not None:
            self._pinned_account_ids = parse_account_id_list(
                getattr(cfg, "RUBIKA_ACCOUNT_IDS", "") or ""
            )
        else:
            self._pinned_account_ids = list(account_ids) if mode in {MODE_PINNED, MODE_SHADOW} else []
        # Legacy attribute: only treat as frozen pin for pinned/shadow modes.
        self._explicit_account_ids = (
            list(self._pinned_account_ids) if mode in {MODE_PINNED, MODE_SHADOW} else None
        )

    def _get_settings(self) -> WorkerSettings:
        return self._settings or get_worker_settings()

    def replace_account_ids(self, account_ids: list[int]) -> None:
        next_ids, _added, _removed = reconcile_worker_account_ids(
            self.account_ids, account_ids
        )
        self.account_ids = next_ids
        self._allowed_account_ids = {str(a) for a in self.account_ids}
        if self.account_ids:
            self._round_robin_index %= len(self.account_ids)
        else:
            self._round_robin_index = 0

    def _resolve_desired_account_ids(self) -> tuple[list[int], dict]:
        settings = self._get_settings()
        return resolve_rubika_worker_account_ids_from_settings(settings)

    async def _refresh_accounts_loop(self) -> None:
        while True:
            await asyncio.sleep(self._account_refresh_interval_seconds or 60)
            try:
                desired, meta = self._resolve_desired_account_ids()
                if meta["mode"] == MODE_SHADOW:
                    self.logger.info(
                        "event=rubika_discovery_shadow pinned=%s dynamic=%s would_add=%s would_remove=%s",
                        ",".join(str(i) for i in meta["pinned_ids"]) or "-",
                        ",".join(str(i) for i in meta["dynamic_eligible_ids"]) or "-",
                        ",".join(str(i) for i in meta["would_add"]) or "-",
                        ",".join(str(i) for i in meta["would_remove"]) or "-",
                    )
                if desired != self.account_ids:
                    before = list(self.account_ids)
                    self.replace_account_ids(desired)
                    self.logger.info(
                        "rubika_pool_accounts_refreshed mode=%s before=%s after=%s",
                        meta["mode"],
                        ",".join(str(i) for i in before) or "-",
                        ",".join(str(i) for i in desired) or "-",
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
            desired, meta = self._resolve_desired_account_ids()
            self.replace_account_ids(desired)
            if meta["mode"] == MODE_SHADOW:
                self.logger.info(
                    "event=rubika_discovery_shadow boot pinned=%s dynamic=%s",
                    ",".join(str(i) for i in meta["pinned_ids"]) or "-",
                    ",".join(str(i) for i in meta["dynamic_eligible_ids"]) or "-",
                )
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
