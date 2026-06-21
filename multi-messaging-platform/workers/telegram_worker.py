"""کارگر تلگرام — dry-run/shadow در فاز ۸؛ ارسال live در مرحله بعد."""

from workers.base_worker import BaseWorker
from workers.config import get_worker_settings
from workers.delivery import deliver_platform_message
from workers.payloads import WorkerPayload, WorkerResult


class TelegramWorker(BaseWorker):
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
            platform="telegram",
            account_id=account_id,
            redis_url=redis_url,
            database_url=database_url,
            poll_interval_seconds=poll_interval_seconds,
            log_level=log_level,
        )

    async def send_message(self, payload: WorkerPayload) -> WorkerResult:
        return await deliver_platform_message(
            self.platform,
            payload,
            get_worker_settings(),
        )
