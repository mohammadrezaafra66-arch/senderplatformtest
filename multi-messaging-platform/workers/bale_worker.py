"""کارگر placeholder بله — بدون ارسال واقعی."""

from workers.base_worker import BaseWorker
from workers.payloads import WorkerPayload, WorkerResult


class BaleWorker(BaseWorker):
    def __init__(
        self,
        *,
        account_id: int | str,
        redis_url: str,
        database_url: str,
        poll_interval_seconds: int = 5,
        log_level: str = "INFO",
    ) -> None:
        super().__init__(
            platform="bale",
            account_id=account_id,
            redis_url=redis_url,
            database_url=database_url,
            poll_interval_seconds=poll_interval_seconds,
            log_level=log_level,
        )

    async def send_message(self, payload: WorkerPayload) -> WorkerResult:
        return WorkerResult(
            success=False,
            status="placeholder_not_implemented",
            error_code="not_implemented",
            error_message="Real platform delivery is not implemented in this phase",
            platform_message_id=None,
            retryable=False,
        )
