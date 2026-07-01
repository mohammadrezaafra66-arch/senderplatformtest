"""پاکسازی فایل‌های روبیکا — فاز ۸ سند.

Celery task هر روز ساعت ۳ صبح (rubika-cleanup-daily در tasks.py).

سیاست (از .env.example / سند بخش پنج):
  RUBIKA_VOICE_RETENTION_DAYS=7     → فایل‌های ویس قدیمی‌تر حذف می‌شوند
  RUBIKA_IMAGE_RETENTION_DAYS=7     → فایل‌های تصویر قدیمی‌تر حذف می‌شوند
  RUBIKA_REPORT_ARCHIVE_DAYS=30     → Word و Excel قدیمی‌تر آرشیو می‌شوند

"آرشیو" = انتقال به زیرپوشه archive/ در همان مسیر (نه حذف).
  storage/transcriptions/rubika/archive/YYYY-MM-DD.docx
  storage/price_reports/rubika/archive/YYYY-MM-DD_*.xlsx

این فایل هرگز به DB دسترسی نمی‌زند — تماماً filesystem است.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("core_engine.services.rubika_cleanup")

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_DIR_VOICES = PROJECT_ROOT / "storage" / "voices" / "rubika"
_DIR_IMAGES = PROJECT_ROOT / "storage" / "images" / "rubika"
_DIR_TRANSCRIPTIONS = PROJECT_ROOT / "storage" / "transcriptions" / "rubika"
_DIR_PRICE_REPORTS = PROJECT_ROOT / "storage" / "price_reports" / "rubika"


def _env_days(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _delete_old_files(directory: Path, older_than_days: int) -> dict:
    """حذف فایل‌های قدیمی‌تر از older_than_days روز در directory."""
    if not directory.exists():
        return {"deleted": 0, "freed_bytes": 0}

    cutoff = datetime.now() - timedelta(days=older_than_days)
    deleted = freed = 0

    for f in directory.iterdir():
        if not f.is_file():
            continue
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                size = f.stat().st_size
                f.unlink()
                deleted += 1
                freed += size
                logger.info("rubika_cleanup: حذف %s (%.1f KB)", f.name, size / 1024)
        except OSError:
            logger.exception("rubika_cleanup: خطا در حذف %s", f)

    return {"deleted": deleted, "freed_bytes": freed}


def _archive_old_files(directory: Path, older_than_days: int, extension_filter: str | None = None) -> dict:
    """انتقال فایل‌های قدیمی به زیرپوشه archive/."""
    if not directory.exists():
        return {"archived": 0}

    cutoff = datetime.now() - timedelta(days=older_than_days)
    archive_dir = directory / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    archived = 0
    for f in directory.iterdir():
        if not f.is_file():
            continue
        if extension_filter and not f.name.endswith(extension_filter):
            continue
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                dest = archive_dir / f.name
                if dest.exists():
                    # اگر فایل از قبل در آرشیو بود، پسوند شماره اضافه کن
                    stem, suffix = f.stem, f.suffix
                    dest = archive_dir / f"{stem}_dup{suffix}"
                f.rename(dest)
                archived += 1
                logger.info("rubika_cleanup: آرشیو %s → archive/%s", f.name, dest.name)
        except OSError:
            logger.exception("rubika_cleanup: خطا در آرشیو %s", f)

    return {"archived": archived}


def run_daily_cleanup() -> dict:
    """پاکسازی کامل — از Celery task فراخوانی می‌شود.

    Note: این تابع sync است (نه async) چون همه عملیات filesystem هستند.
    Celery task مستقیماً فراخوانی می‌کند بدون asyncio.run().
    """
    voice_days = _env_days("RUBIKA_VOICE_RETENTION_DAYS", 7)
    image_days = _env_days("RUBIKA_IMAGE_RETENTION_DAYS", 7)
    report_days = _env_days("RUBIKA_REPORT_ARCHIVE_DAYS", 30)

    voice_result = _delete_old_files(_DIR_VOICES, older_than_days=voice_days)
    image_result = _delete_old_files(_DIR_IMAGES, older_than_days=image_days)
    transcription_result = _archive_old_files(
        _DIR_TRANSCRIPTIONS, older_than_days=report_days, extension_filter=".docx"
    )
    price_result = _archive_old_files(
        _DIR_PRICE_REPORTS, older_than_days=report_days, extension_filter=".xlsx"
    )

    total_freed_mb = (
        voice_result["freed_bytes"] + image_result["freed_bytes"]
    ) / (1024 * 1024)

    summary = {
        "voices_deleted": voice_result["deleted"],
        "images_deleted": image_result["deleted"],
        "freed_mb": round(total_freed_mb, 2),
        "transcriptions_archived": transcription_result["archived"],
        "price_reports_archived": price_result["archived"],
        "config": {
            "voice_retention_days": voice_days,
            "image_retention_days": image_days,
            "report_archive_days": report_days,
        },
    }

    logger.info(
        "rubika_cleanup: "
        "voices=%d images=%d freed=%.1fMB archived(docx=%d xlsx=%d)",
        voice_result["deleted"],
        image_result["deleted"],
        total_freed_mb,
        transcription_result["archived"],
        price_result["archived"],
    )
    return summary
