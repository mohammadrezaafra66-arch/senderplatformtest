"""Structured GPT variation failures. Fail closed for use_gpt campaigns."""

from __future__ import annotations

PERSIAN_GPT_FAILED = (
    "تولید متن‌های متنوع با GPT انجام نشد؛ آماده‌سازی کمپین متوقف شد."
)
PERSIAN_NOT_CONFIGURED = "سرویس تولید متن GPT هنوز تنظیم نشده است."
PERSIAN_TIMEOUT = "دریافت پیشنهاد متن از GPT بیش از حد طول کشید."
PERSIAN_INVALID = "پاسخ GPT قابل استفاده نبود؛ لطفاً دوباره تلاش کنید."
PERSIAN_RATE_LIMITED = "سرویس GPT موقتاً در دسترس نیست؛ لطفاً کمی بعد دوباره تلاش کنید."

GPT_NOT_CONFIGURED = "GPT_NOT_CONFIGURED"
GPT_UNAVAILABLE = "GPT_UNAVAILABLE"
GPT_TIMEOUT = "GPT_TIMEOUT"
GPT_RATE_LIMITED = "GPT_RATE_LIMITED"
GPT_INVALID_RESPONSE = "GPT_INVALID_RESPONSE"
GPT_VARIATION_INVALID = "GPT_VARIATION_INVALID"
GPT_INSUFFICIENT_VARIATIONS = "GPT_INSUFFICIENT_VARIATIONS"

GPT_ERROR_CODES = frozenset(
    {
        GPT_NOT_CONFIGURED,
        GPT_UNAVAILABLE,
        GPT_TIMEOUT,
        GPT_RATE_LIMITED,
        GPT_INVALID_RESPONSE,
        GPT_VARIATION_INVALID,
        GPT_INSUFFICIENT_VARIATIONS,
    }
)

_DEFAULT_MESSAGES = {
    GPT_NOT_CONFIGURED: PERSIAN_NOT_CONFIGURED,
    GPT_TIMEOUT: PERSIAN_TIMEOUT,
    GPT_VARIATION_INVALID: PERSIAN_INVALID,
    GPT_INVALID_RESPONSE: PERSIAN_INVALID,
    GPT_INSUFFICIENT_VARIATIONS: PERSIAN_INVALID,
    GPT_RATE_LIMITED: PERSIAN_RATE_LIMITED,
    GPT_UNAVAILABLE: PERSIAN_GPT_FAILED,
}


class GptVariationError(Exception):
    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        details: dict | None = None,
    ) -> None:
        self.code = code
        self.message = message or _DEFAULT_MESSAGES.get(code, PERSIAN_GPT_FAILED)
        self.details = details or {}
        super().__init__(self.message)

    def http_detail(self) -> dict:
        payload = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload
