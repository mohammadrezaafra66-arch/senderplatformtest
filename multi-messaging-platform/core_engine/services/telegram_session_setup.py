"""راه‌اندازی نشست تلگرام برای حساب‌های MTProto."""

from __future__ import annotations

from typing import Any


async def verify_phone_code(db: Any, *, account_id: int, phone_number: str, code: str) -> dict[str, Any]:
    """اعتبارسنجی کد تأیید شماره تلگرام."""
    return {
        "status": "error",
        "message": "تابع پشتیبانی نشده است.",
        "account_id": account_id,
        "phone_number": phone_number,
        "code": code,
    }
