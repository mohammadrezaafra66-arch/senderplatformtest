"""خطاهای پایه Worker."""


class WorkerError(Exception):
    """خطای عمومی Worker."""


class RetryableWorkerError(WorkerError):
    """خطایی که می‌تواند با retry حل شود."""


class PermanentWorkerError(WorkerError):
    """خطای دائمی که retry بی‌فایده است."""


class RateLimitWorkerError(RetryableWorkerError):
    """محدودیت نرخ ارسال فعال شده است."""


class SessionInvalidError(PermanentWorkerError):
    """نشست یا توکن حساب نامعتبر است."""


class PayloadValidationError(PermanentWorkerError):
    """payload ورودی نامعتبر است."""
