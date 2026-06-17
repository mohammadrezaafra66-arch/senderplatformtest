"""کلاس پایه Worker — منطق عملیاتی مشترک کانال‌ها.

NOT for Phase 4 campaign staging. Redis lpush/rpop here targets worker delivery
queues only. Phase 4 must use database staging only — do not invoke this module
from core_engine debug prepare endpoints. See core_engine.services.safety_guard.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any

from pydantic import ValidationError
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

REQUIRED_PAYLOAD_FIELDS = (
    "message_id",
    "campaign_id",
    "contact_id",
    "account_id",
    "platform",
    "recipient",
    "recipient_type",
    "message_text",
    "dedupe_key",
)


def _is_redis_true(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, bytes):
        value = value.decode()
    return str(value).strip().lower() == "true"


class BaseWorker(ABC):
    def __init__(
        self,
        *,
        platform: str,
        account_id: int | str,
        redis_url: str,
        database_url: str,
        poll_interval_seconds: int = 5,
        log_level: str = "INFO",
    ) -> None:
        self.platform = platform
        self.account_id = account_id
        self.redis_url = redis_url
        self.database_url = database_url
        self.poll_interval_seconds = poll_interval_seconds
        self.logger = get_worker_logger(
            self.__class__.__name__,
            platform=platform,
            account_id=account_id,
            level=log_level,
        )
        self._redis: Redis | None = None

    async def connect(self) -> None:
        self._redis = Redis.from_url(self.redis_url, decode_responses=True)
        await self._redis.ping()
        log_worker_event(
            self.logger,
            event="worker_connected",
            status="ok",
            platform=self.platform,
            account_id=self.account_id,
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
            account_id=self.account_id,
        )

    @property
    def redis(self) -> Redis:
        if self._redis is None:
            raise RuntimeError("Worker Redis client is not connected.")
        return self._redis

    def get_queue_key(self) -> str:
        return queue_key(self.platform, self.account_id)

    async def check_kill_switch(self) -> bool:
        value = await self.redis.get(kill_switch_key())
        if not _is_redis_true(value):
            return False
        log_worker_event(
            self.logger,
            event="paused_by_kill_switch",
            status="paused",
            platform=self.platform,
            account_id=self.account_id,
        )
        return True

    async def check_account_paused(self) -> bool:
        value = await self.redis.get(account_pause_key(self.account_id))
        if not _is_redis_true(value):
            return False
        log_worker_event(
            self.logger,
            event="account_paused",
            status="paused",
            platform=self.platform,
            account_id=self.account_id,
        )
        return True

    async def check_campaign_paused(
        self,
        payload: WorkerPayload,
        raw_payload: str,
    ) -> bool:
        value = await self.redis.get(campaign_pause_key(payload.campaign_id))
        if not _is_redis_true(value):
            return False
        await self.redis.lpush(self.get_queue_key(), raw_payload)
        log_worker_event(
            self.logger,
            event="campaign_paused_requeued",
            status="requeued",
            message_id=payload.message_id,
            campaign_id=payload.campaign_id,
            platform=self.platform,
            account_id=self.account_id,
        )
        return True

    async def read_next_payload(self) -> str | None:
        raw = await self.redis.lpop(self.get_queue_key())
        if raw is None:
            log_worker_event(
                self.logger,
                event="queue_empty",
                status="idle",
                platform=self.platform,
                account_id=self.account_id,
            )
            return None

        log_worker_event(
            self.logger,
            event="payload_read",
            status="read",
            platform=self.platform,
            account_id=self.account_id,
        )
        return raw

    async def validate_payload(self, raw_payload: str | dict[str, Any]) -> WorkerPayload:
        data: dict[str, Any]
        if isinstance(raw_payload, str):
            try:
                parsed = json.loads(raw_payload)
            except json.JSONDecodeError as exc:
                log_worker_event(
                    self.logger,
                    event="payload_invalid_json",
                    status="invalid",
                    platform=self.platform,
                    account_id=self.account_id,
                    error_code="invalid_json",
                    error_message=str(exc),
                    level=40,
                )
                raise PayloadValidationError("Queue item is not valid JSON.") from exc
            if not isinstance(parsed, dict):
                raise PayloadValidationError("Queue item JSON must be an object.")
            data = parsed
        else:
            data = raw_payload

        missing = [field for field in REQUIRED_PAYLOAD_FIELDS if not data.get(field)]
        if missing:
            raise PayloadValidationError(
                f"Missing required payload fields: {', '.join(missing)}"
            )

        try:
            payload = WorkerPayload.model_validate(data)
        except ValidationError as exc:
            raise PayloadValidationError(f"Invalid worker payload: {exc}") from exc

        if payload.platform != self.platform:
            raise PayloadValidationError(
                f"Payload platform '{payload.platform}' does not match worker '{self.platform}'."
            )
        if str(payload.account_id) != str(self.account_id):
            raise PayloadValidationError(
                f"Payload account_id '{payload.account_id}' does not match worker '{self.account_id}'."
            )
        if not str(payload.recipient).strip():
            raise PayloadValidationError("recipient is required.")
        if not str(payload.message_text).strip():
            raise PayloadValidationError("message_text is required.")
        if not str(payload.dedupe_key).strip():
            raise PayloadValidationError("dedupe_key is required.")

        log_worker_event(
            self.logger,
            event="payload_validated",
            status="validated",
            message_id=payload.message_id,
            campaign_id=payload.campaign_id,
            platform=self.platform,
            account_id=self.account_id,
        )
        return payload

    @abstractmethod
    async def send_message(self, payload: WorkerPayload) -> WorkerResult:
        """ارسال پیام به پلتفرم — در این مرحله placeholder است."""

    async def handle_result(self, payload: WorkerPayload, result: WorkerResult) -> None:
        event = (
            "message_processed_success"
            if result.success
            else "message_processed_failed"
        )
        log_worker_event(
            self.logger,
            event=event,
            status=result.status,
            message_id=payload.message_id,
            campaign_id=payload.campaign_id,
            error_code=result.error_code,
            error_message=result.error_message,
            platform=self.platform,
            account_id=self.account_id,
        )
        update_message_attempt_result(
            message_id=payload.message_id,
            attempt_no=payload.attempt,
            status=result.status,
            platform_message_id=result.platform_message_id,
            error_code=result.error_code,
            error_message=result.error_message,
        )

    async def handle_error(
        self,
        payload: WorkerPayload | None,
        error: Exception,
    ) -> None:
        error_code = getattr(error, "error_code", None) or error.__class__.__name__
        error_message = str(error)
        log_worker_event(
            self.logger,
            event="worker_error",
            status="failed",
            message_id=getattr(payload, "message_id", None),
            campaign_id=getattr(payload, "campaign_id", None),
            error_code=str(error_code),
            error_message=error_message,
            platform=self.platform,
            account_id=self.account_id,
            level=40,
        )
        db_log_worker_event(
            event="worker_error",
            platform=self.platform,
            account_id=self.account_id,
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
        try:
            if await self.check_kill_switch():
                return
            if await self.check_account_paused():
                return

            raw = await self.read_next_payload()
            if raw is None:
                return

            payload = await self.validate_payload(raw)

            if await self.check_campaign_paused(payload, raw):
                return

            result = await self.send_message(payload)
            await self.handle_result(payload, result)
        except WorkerError as exc:
            await self.handle_error(payload, exc)
        except Exception as exc:
            await self.handle_error(payload, exc)

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
