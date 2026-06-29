"""Base class for workers that poll multiple account queues in one process."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from redis.asyncio import Redis

from workers.db import log_worker_event as db_log_worker_event
from workers.db import update_message_attempt_result
from workers.errors import PayloadValidationError, WorkerError
from workers.logging_utils import get_worker_logger, log_worker_event
from workers.payloads import WorkerPayload, WorkerResult
from workers.redis_keys import (
    account_pause_key,
    campaign_pause_key,
    kill_switch_key,
    queue_key,
)
from workers.retry import (
    build_retry_queue_payload,
    compute_retry_delay_seconds,
    should_schedule_retry,
)
from workers.worker_runtime import is_redis_truthy, validate_worker_payload


class MultiAccountWorker(ABC):
    """Poll Redis queues for multiple accounts using fair round-robin."""

    def __init__(
        self,
        *,
        platform: str,
        account_ids: list[int],
        redis_url: str,
        database_url: str,
        poll_interval_seconds: int = 5,
        log_level: str = "INFO",
        max_retry_attempts: int = 0,
        retry_base_delay_seconds: float = 5.0,
    ) -> None:
        if not account_ids:
            raise ValueError("account_ids must not be empty.")

        self.platform = platform
        self.account_ids = sorted({int(account_id) for account_id in account_ids})
        self._allowed_account_ids = {str(account_id) for account_id in self.account_ids}
        self.redis_url = redis_url
        self.database_url = database_url
        self.poll_interval_seconds = poll_interval_seconds
        self.logger = get_worker_logger(
            self.__class__.__name__,
            platform=platform,
            account_id=",".join(str(account_id) for account_id in self.account_ids),
            level=log_level,
        )
        self._redis: Redis | None = None
        self._round_robin_index = 0
        self._max_retry_attempts = max_retry_attempts
        self._retry_base_delay_seconds = retry_base_delay_seconds

    async def connect(self) -> None:
        self._redis = Redis.from_url(self.redis_url, decode_responses=True)
        await self._redis.ping()
        log_worker_event(
            self.logger,
            event="worker_connected",
            status="ok",
            platform=self.platform,
            account_id=",".join(str(account_id) for account_id in self.account_ids),
        )

    async def disconnect(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
        log_worker_event(
            self.logger,
            event="worker_disconnected",
            status="ok",
            platform=self.platform,
            account_id=",".join(str(account_id) for account_id in self.account_ids),
        )

    @property
    def redis(self) -> Redis:
        if self._redis is None:
            raise RuntimeError("Worker Redis client is not connected.")
        return self._redis

    def queue_key_for(self, account_id: int | str) -> str:
        return queue_key(self.platform, account_id)

    async def check_kill_switch(self) -> bool:
        value = await self.redis.get(kill_switch_key())
        if not is_redis_truthy(value):
            return False
        log_worker_event(
            self.logger,
            event="paused_by_kill_switch",
            status="paused",
            platform=self.platform,
        )
        return True

    async def check_account_paused(self, account_id: int | str) -> bool:
        value = await self.redis.get(account_pause_key(account_id))
        if not is_redis_truthy(value):
            return False
        log_worker_event(
            self.logger,
            event="account_paused",
            status="paused",
            platform=self.platform,
            account_id=account_id,
        )
        return True

    async def check_campaign_paused(
        self,
        payload: WorkerPayload,
        raw_payload: str,
        *,
        account_id: int | str,
    ) -> bool:
        value = await self.redis.get(campaign_pause_key(payload.campaign_id))
        if not is_redis_truthy(value):
            return False
        await self.redis.lpush(self.queue_key_for(account_id), raw_payload)
        log_worker_event(
            self.logger,
            event="campaign_paused_requeued",
            status="requeued",
            message_id=payload.message_id,
            campaign_id=payload.campaign_id,
            platform=self.platform,
            account_id=account_id,
        )
        return True

    async def read_next_payload(self) -> tuple[int, str] | None:
        """Round-robin LPOP across assigned account queues."""
        account_count = len(self.account_ids)
        for step in range(account_count):
            index = (self._round_robin_index + step) % account_count
            account_id = self.account_ids[index]
            raw = await self.redis.lpop(self.queue_key_for(account_id))
            if raw is None:
                continue

            self._round_robin_index = (index + 1) % account_count
            log_worker_event(
                self.logger,
                event="payload_read",
                status="read",
                platform=self.platform,
                account_id=account_id,
            )
            return account_id, raw

        log_worker_event(
            self.logger,
            event="queue_empty",
            status="idle",
            platform=self.platform,
            account_id=",".join(str(account_id) for account_id in self.account_ids),
        )
        return None

    async def validate_payload(self, raw_payload: str | dict[str, Any]) -> WorkerPayload:
        return validate_worker_payload(
            raw_payload,
            platform=self.platform,
            allowed_account_ids=self._allowed_account_ids,
            logger=self.logger,
        )

    @abstractmethod
    async def send_message(self, payload: WorkerPayload) -> WorkerResult:
        """Deliver a validated payload to the platform."""

    async def handle_result(
        self,
        payload: WorkerPayload,
        result: WorkerResult,
        *,
        raw_payload: str | None = None,
        account_id: int | str | None = None,
    ) -> None:
        event = "message_processed_success" if result.success else "message_processed_failed"
        log_worker_event(
            self.logger,
            event=event,
            status=result.status,
            message_id=payload.message_id,
            campaign_id=payload.campaign_id,
            error_code=result.error_code,
            error_message=result.error_message,
            platform=self.platform,
            account_id=payload.account_id,
        )
        update_message_attempt_result(
            message_id=payload.message_id,
            attempt_no=payload.attempt,
            status=result.status,
            platform_message_id=result.platform_message_id,
            error_code=result.error_code,
            error_message=result.error_message,
            campaign_id=payload.campaign_id,
            contact_id=payload.contact_id,
            account_id=payload.account_id,
            failure_reason=result.error_message,
            success=result.success,
        )

        if (
            raw_payload is not None
            and account_id is not None
            and should_schedule_retry(
                payload,
                result,
                max_retry_attempts=self._max_retry_attempts,
            )
        ):
            await self._schedule_retry(raw_payload, payload, account_id=account_id)

    async def _schedule_retry(
        self,
        raw_payload: str,
        payload: WorkerPayload,
        *,
        account_id: int | str,
    ) -> None:
        delay_seconds = compute_retry_delay_seconds(
            payload.attempt + 1,
            self._retry_base_delay_seconds,
        )
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        retry_payload = build_retry_queue_payload(raw_payload, payload)
        await self.redis.rpush(self.queue_key_for(account_id), retry_payload)
        log_worker_event(
            self.logger,
            event="message_retry_scheduled",
            status="retry_scheduled",
            message_id=payload.message_id,
            campaign_id=payload.campaign_id,
            platform=self.platform,
            account_id=account_id,
            error_message=f"attempt={payload.attempt + 1}",
        )

    async def handle_error(
        self,
        payload: WorkerPayload | None,
        error: Exception,
        *,
        account_id: int | str | None = None,
    ) -> None:
        error_code = getattr(error, "error_code", None) or error.__class__.__name__
        error_message = str(error)
        resolved_account_id = account_id or getattr(payload, "account_id", None)
        log_worker_event(
            self.logger,
            event="worker_error",
            status="failed",
            message_id=getattr(payload, "message_id", None),
            campaign_id=getattr(payload, "campaign_id", None),
            error_code=str(error_code),
            error_message=error_message,
            platform=self.platform,
            account_id=resolved_account_id,
            level=40,
        )
        db_log_worker_event(
            event="worker_error",
            platform=self.platform,
            account_id=resolved_account_id,
            message_id=getattr(payload, "message_id", None),
            campaign_id=getattr(payload, "campaign_id", None),
            status="failed",
            error_code=str(error_code),
            error_message=error_message,
        )

    async def run_once(self) -> None:
        if self._redis is None:
            await self.connect()

        payload: WorkerPayload | None = None
        active_account_id: int | None = None
        try:
            if await self.check_kill_switch():
                return

            item = await self.read_next_payload()
            if item is None:
                return

            active_account_id, raw = item
            if await self.check_account_paused(active_account_id):
                await self.redis.rpush(self.queue_key_for(active_account_id), raw)
                return

            payload = await self.validate_payload(raw)

            if await self.check_campaign_paused(
                payload,
                raw,
                account_id=active_account_id,
            ):
                return

            result = await self.send_message(payload)
            await self.handle_result(
                payload,
                result,
                raw_payload=raw,
                account_id=active_account_id,
            )
        except PayloadValidationError as exc:
            await self.handle_error(payload, exc, account_id=active_account_id)
        except WorkerError as exc:
            await self.handle_error(payload, exc, account_id=active_account_id)
        except Exception as exc:
            await self.handle_error(payload, exc, account_id=active_account_id)

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
