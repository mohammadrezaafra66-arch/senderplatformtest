"""پردازش تصاویر دریافتی گروه‌های روبیکا با GPT-4o vision — فاز ۶ سند.

اجرا: Celery task هر دقیقه (process-rubika-images-every-minute در tasks.py).

جریان:
۱. پیام‌های image با image_extracted_text=None از RubikaGroupMessage بخوان.
۲. تصویر را base64 کن و به GPT-4o vision بده با prompt استخراج قیمت/اطلاعات تجاری.
۳. اگر حاوی اطلاعات مرتبط بود → image_extracted_text را ذخیره کن.
۴. اگر حاوی اطلاعات مرتبط نبود → image_extracted_text = "[بدون اطلاعات تجاری]"
   تا task بعدی دوباره پردازش نکند.
۵. ردیف‌های با اطلاعات تجاری → ai_analyzed=False باقی می‌مانند تا
   rubika_ai_analyzer.py در فاز ۷ قیمت‌ها را استخراج کند.

ملاحظه: GPT-4o vision هزینه‌بر است. RUBIKA_IMAGE_PROCESSOR_ENABLED=false
  را در .env نگه داری تا زمانی که سیستم آزمایش شده و محدودیت‌گذاری شود.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

from sqlalchemy.orm import Session

from core_engine.models import RubikaGroupMessage

logger = logging.getLogger("core_engine.services.rubika_image_processor")

_BATCH_SIZE = 10  # تصاویر کمتر از ویس در هر batch — هزینه API بالاتر است
_NO_BUSINESS_INFO = "[بدون اطلاعات تجاری]"
_FAILED_PLACEHOLDER = "[پردازش ناموفق]"

_EXTRACTION_PROMPT = """این تصویر را از یک گروه تجاری روبیکا تحلیل کن.
اگر حاوی قیمت، لیست محصول، قیمت‌نامه، یا هر اطلاعات تجاری/بازاری است:
  — اطلاعات را به‌صورت فارسی ساختاریافته استخراج کن (نام محصول: قیمت).
اگر هیچ اطلاعات تجاری/قیمتی ندارد:
  — فقط بنویس: [بدون اطلاعات تجاری]

پاسخ فقط متن استخراج‌شده باشد، بدون مقدمه."""


async def _analyze_image_with_gpt4o(image_path: str) -> str:
    """تصویر را base64 کرده، به GPT-4o vision می‌فرستد و متن استخراج‌شده برمی‌گرداند."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.warning("rubika_image: OPENAI_API_KEY تنظیم نشده")
        return _FAILED_PLACEHOLDER

    try:
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")
    except OSError:
        logger.exception("rubika_image: باز کردن فایل %s ناموفق", image_path)
        return _FAILED_PLACEHOLDER

    # تشخیص نوع MIME از پسوند
    ext = Path(image_path).suffix.lower().lstrip(".")
    mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
    media_type = mime_map.get(ext, "image/jpeg")

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-4o",
            max_tokens=1000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{image_data}",
                                "detail": "low",  # low = ارزان‌تر، برای استخراج متن کافی است
                            },
                        },
                        {"type": "text", "text": _EXTRACTION_PROMPT},
                    ],
                }
            ],
        )
        text = (response.choices[0].message.content or "").strip()
        logger.info(
            "rubika_image: GPT-4o responded (%d chars) for %s", len(text), image_path
        )
        return text or _NO_BUSINESS_INFO

    except Exception:
        logger.exception("rubika_image: GPT-4o API call failed for %s", image_path)
        return _FAILED_PLACEHOLDER


async def process_pending_images(db: Session) -> dict:
    """Celery task از این تابع مستقیم فراخوانی می‌کند.

    Returns:
        dict با کلیدهای processed, has_business_info, no_info, failed, skipped_missing_file
    """
    enabled = os.getenv("RUBIKA_IMAGE_PROCESSOR_ENABLED", "false").strip().lower()
    if enabled != "true":
        return {"skipped": True, "reason": "RUBIKA_IMAGE_PROCESSOR_ENABLED != true"}

    rows = (
        db.query(RubikaGroupMessage)
        .filter(
            RubikaGroupMessage.message_type == "image",
            RubikaGroupMessage.image_file_path.isnot(None),
            RubikaGroupMessage.image_extracted_text.is_(None),
        )
        .limit(_BATCH_SIZE)
        .all()
    )

    if not rows:
        return {"processed": 0, "has_business_info": 0, "no_info": 0, "failed": 0, "skipped_missing_file": 0}

    processed = has_info = no_info = failed = skipped = 0

    for row in rows:
        file_path = row.image_file_path or ""
        if not Path(file_path).exists():
            logger.warning("rubika_image: فایل %s وجود ندارد — skip", file_path)
            row.image_extracted_text = _FAILED_PLACEHOLDER
            db.flush()
            skipped += 1
            continue

        try:
            extracted = await _analyze_image_with_gpt4o(file_path)
            row.image_extracted_text = extracted

            if extracted not in (_NO_BUSINESS_INFO, _FAILED_PLACEHOLDER):
                has_info += 1
                # ai_analyzed=False باقی می‌ماند — rubika_ai_analyzer.py (فاز ۷) آن را pick up می‌کند
                logger.info(
                    "rubika_image: اطلاعات تجاری در پیام %s یافت شد", row.id
                )
            else:
                no_info += 1
                # اگر اطلاعاتی نبود، دیگر نیازی به تحلیل AI نیست
                row.ai_analyzed = True

            db.flush()
            processed += 1

        except Exception:
            logger.exception("rubika_image: پردازش پیام %s ناموفق", row.id)
            row.image_extracted_text = _FAILED_PLACEHOLDER
            db.flush()
            failed += 1

    db.commit()
    logger.info(
        "rubika_image: processed=%d has_info=%d no_info=%d failed=%d skipped=%d",
        processed, has_info, no_info, failed, skipped,
    )
    return {
        "processed": processed,
        "has_business_info": has_info,
        "no_info": no_info,
        "failed": failed,
        "skipped_missing_file": skipped,
    }
