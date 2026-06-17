"""آماده‌سازی کلاینت OpenAI — بدون ارسال درخواست واقعی در این مرحله."""

from openai import OpenAI

from core_engine.config import Settings, get_settings

PLACEHOLDER_API_KEYS = frozenset(
    {
        "",
        "your_openai_api_key_here",
    }
)


def is_openai_api_key_configured(settings: Settings | None = None) -> bool:
    current = settings or get_settings()
    return current.OPENAI_API_KEY.strip() not in PLACEHOLDER_API_KEYS


def get_openai_client(settings: Settings | None = None) -> OpenAI:
    """کلاینت OpenAI را برمی‌گرداند؛ فقط هنگام فراخوانی API key را بررسی می‌کند."""
    current = settings or get_settings()
    if not is_openai_api_key_configured(current):
        raise ValueError(
            "OPENAI_API_KEY is not configured. Set a valid key in environment or .env file."
        )
    return OpenAI(
        api_key=current.OPENAI_API_KEY,
        timeout=current.OPENAI_TIMEOUT_SECONDS,
        max_retries=current.OPENAI_MAX_RETRIES,
    )
