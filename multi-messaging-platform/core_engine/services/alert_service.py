"""سرویس ارسال هشدار به تلگرام ادمین — برای رویدادهای مهم اکانت واتساپ.

هرگز exception بالا نمی‌دهد؛ در بدترین حالت فقط لاگ می‌کند و False برمی‌گرداند
تا مسیر اصلی (worker / webhook) نشکند.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API_TIMEOUT_SECONDS = 10

# نگاشت event_type به قالب پیام فارسی
_MESSAGE_TEMPLATES: dict[str, str] = {
    "blocked": "🔴 اکانت {account_id} بلاک شد (401) — نیاز به QR جدید. {detail}",
    "yellow_card": (
        "🟡 هشدار پیش‌بن اکانت {account_id} — {detail} قطعی در پنجره کوتاه. "
        "کاهش ارسال."
    ),
    "long_disconnect": "🟠 اکانت {account_id} مدت طولانی قطع است. {detail}",
    "reconnect_failed": (
        "❌ اکانت {account_id}: شکست reconnect پس از حداکثر تلاش. {detail}"
    ),
    "recovered": "✅ اکانت {account_id} بازگشت به authorized. {detail}",
    "daily_cap_reached": (
        "🛑 اکانت {account_id} به سقف روزانه رسید ({detail}). ارسال تا فردا متوقف."
    ),
}


async def send_alert(event_type: str, account_id: int, detail: str = "") -> bool:
    """یک هشدار به تلگرام ادمین می‌فرستد.

    Returns:
        True اگر پیام با موفقیت ارسال شد، در غیر این صورت False.
        هرگز exception بالا نمی‌دهد.
    """
    try:
        token = os.environ.get("ADMIN_TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("ADMIN_TELEGRAM_CHAT_ID")

        if not token or not chat_id:
            logger.warning(
                "alert_skipped_no_config event=%s account_id=%s "
                "(ADMIN_TELEGRAM_BOT_TOKEN یا ADMIN_TELEGRAM_CHAT_ID ست نشده)",
                event_type, account_id,
            )
            return False

        template = _MESSAGE_TEMPLATES.get(
            event_type,
            "ℹ️ رویداد اکانت {account_id}: " + event_type + " {detail}",
        )
        text = template.format(account_id=account_id, detail=detail).strip()

        # timestamp UTC انتهای پیام
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        text = f"{text}\n🕒 {ts}"

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with httpx.AsyncClient(timeout=TELEGRAM_API_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                url,
                data={
                    "chat_id": chat_id,
                    "text": text,
                    # parse_mode عمداً حذف شد — متن ساده است و با HTML مود اگر
                    # detail کاراکتر < > & داشته باشد تلگرام پیام را رد می‌کند.
                },
            )

        if resp.status_code == 200:
            logger.info(
                "alert_sent event=%s account_id=%s", event_type, account_id
            )
            return True

        logger.error(
            "alert_failed event=%s account_id=%s http_status=%s body=%s",
            event_type, account_id, resp.status_code, resp.text[:200],
        )
        return False

    except Exception as exc:  # noqa: BLE001 — هرگز نباید مسیر اصلی را بشکند
        logger.error(
            "alert_error event=%s account_id=%s err=%s",
            event_type, account_id, str(exc),
        )
        return False
