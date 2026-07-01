"""وظایف پس‌زمینه Celery."""

import logging
import os
from typing import Any

from celery import Celery
from celery.schedules import crontab, schedule

logger = logging.getLogger(__name__)

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery("core_engine", broker=redis_url, backend=redis_url)

celery_app.conf.beat_schedule = {
    "push-ready-staged-items-every-5-seconds": {
        "task": "push_ready_staged_items",
        "schedule": schedule(run_every=5),
    },
    "consume-whatsapp-baileys-results-every-10-seconds": {
        "task": "consume_whatsapp_baileys_results",
        "schedule": schedule(run_every=10),
    },
    "consume-whatsapp-session-status-every-10-seconds": {
        "task": "consume_whatsapp_baileys_session_status",
        "schedule": schedule(run_every=10),
    },
    "process-rubika-voices-every-minute": {
        "task": "process_rubika_voices",
        "schedule": schedule(run_every=60),
    },
    "process-rubika-images-every-minute": {
        "task": "process_rubika_images",
        "schedule": schedule(run_every=60),
    },
    "rubika-complaint-check": {
        "task": "rubika_complaint_check",
        "schedule": schedule(run_every=300),
    },
    "rubika-ai-daily": {
        "task": "rubika_daily_ai_analysis",
        "schedule": crontab(hour=22, minute=0),
    },
}


@celery_app.task
def add_numbers(a: int, b: int) -> int:
    return a + b


@celery_app.task(bind=True, max_retries=3, default_retry_delay=1)
def send_message_task(self, payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a message payload with dry-run / shadow mode handling."""
    from core_engine.services.message_dispatch import dispatch_message

    try:
        return dispatch_message(payload)
    except Exception as exc:
        logger.exception("send_message_task failed (attempt %s)", self.request.retries + 1)
        raise self.retry(exc=exc)


@celery_app.task(name="push_ready_staged_items")
def push_ready_staged_items_task():
    """Periodic task to push READY items from RUNNING campaigns to Redis worker queues."""
    import asyncio

    from core_engine.database import SessionLocal
    from core_engine.services.queue_bridge import push_staged_items_to_worker_queue

    session = SessionLocal()
    try:
        result = asyncio.run(push_staged_items_to_worker_queue(session, batch_size=500))
        return result
    except Exception as exc:
        logger.exception("push_ready_staged_items_task failed: %s", exc)
        return {"error": str(exc)}
    finally:
        session.close()


@celery_app.task(name="consume_whatsapp_baileys_results")
def consume_whatsapp_baileys_results_task():
    """Drain whatsapp:results (Baileys Node workers) into audit_logs."""
    from core_engine.config import get_settings
    from core_engine.services.baileys_results_consumer import consume_whatsapp_results_batch

    if get_settings().WHATSAPP_DELIVERY_MODE.strip().lower() != "baileys":
        return {"processed": 0, "skipped": True}

    try:
        return consume_whatsapp_results_batch()
    except Exception as exc:
        logger.exception("consume_whatsapp_baileys_results_task failed: %s", exc)
        return {"error": str(exc)}


@celery_app.task(name="consume_whatsapp_baileys_session_status")
def consume_whatsapp_baileys_session_status_task():
    """Apply session_invalid events → channel_sessions + account status."""
    from core_engine.config import get_settings
    from core_engine.services.baileys_results_consumer import (
        consume_whatsapp_session_status_batch,
    )

    if get_settings().WHATSAPP_DELIVERY_MODE.strip().lower() != "baileys":
        return {"processed": 0, "skipped": True}

    try:
        return consume_whatsapp_session_status_batch()
    except Exception as exc:
        logger.exception("consume_whatsapp_baileys_session_status_task failed: %s", exc)
        return {"error": str(exc)}



@celery_app.task(name="process_rubika_voices")
def process_rubika_voices_task():
    """هر دقیقه: ویس‌های بدون transcription را با Whisper پردازش می‌کند."""
    import asyncio
    from core_engine.database import SessionLocal
    from core_engine.services.rubika_voice_processor import process_pending_voices

    session = SessionLocal()
    try:
        return asyncio.run(process_pending_voices(session))
    except Exception as exc:
        logger.exception("process_rubika_voices_task failed: %s", exc)
        return {"error": str(exc)}
    finally:
        session.close()


@celery_app.task(name="process_rubika_images")
def process_rubika_images_task():
    """هر دقیقه: تصاویر بدون image_extracted_text را با GPT-4o vision پردازش می‌کند."""
    import asyncio
    from core_engine.database import SessionLocal
    from core_engine.services.rubika_image_processor import process_pending_images

    session = SessionLocal()
    try:
        return asyncio.run(process_pending_images(session))
    except Exception as exc:
        logger.exception("process_rubika_images_task failed: %s", exc)
        return {"error": str(exc)}
    finally:
        session.close()


@celery_app.task(name="rubika_complaint_check")
def rubika_complaint_check_task():
    """هر ۵ دقیقه: پیام‌های ai_analyzed=False را برای شکایت بررسی می‌کند."""
    import asyncio
    from core_engine.database import SessionLocal
    from core_engine.models import RubikaGroupMessage
    from core_engine.services.rubika_ai_analyzer import detect_complaints_and_alert

    session = SessionLocal()
    try:
        pending = (
            session.query(RubikaGroupMessage)
            .filter(
                RubikaGroupMessage.ai_analyzed.is_(False),
                RubikaGroupMessage.message_type.in_(["text", "voice"]),
            )
            .limit(50)
            .all()
        )
        complaints = 0
        for row in pending:
            try:
                is_c = asyncio.run(
                    detect_complaints_and_alert(session, message_id=row.id)
                )
                if is_c:
                    complaints += 1
            except Exception as e:
                logger.warning("rubika_complaint_check skip msg %s: %s", row.id, e)
        session.commit()
        return {"checked": len(pending), "complaints": complaints}
    except Exception as exc:
        logger.exception("rubika_complaint_check_task failed: %s", exc)
        return {"error": str(exc)}
    finally:
        session.close()


@celery_app.task(name="rubika_daily_ai_analysis")
def rubika_daily_ai_analysis_task():
    """هر شب ساعت ۲۲: استخراج قیمت + خلاصه روزانه برای همه گروه‌های فعال."""
    import asyncio
    from core_engine.database import SessionLocal
    from core_engine.models import RubikaAllowedGroup
    from core_engine.services.rubika_ai_analyzer import (
        extract_prices_from_messages,
        generate_daily_summary,
    )

    session = SessionLocal()
    try:
        groups = (
            session.query(RubikaAllowedGroup)
            .filter(RubikaAllowedGroup.is_active.is_(True))
            .all()
        )
        results = []
        for group in groups:
            try:
                price_result = asyncio.run(
                    extract_prices_from_messages(session, group_guid=group.group_guid)
                )
                summary = asyncio.run(
                    generate_daily_summary(session, group_guid=group.group_guid)
                )
                results.append({
                    "group_guid": group.group_guid,
                    "price_rows": price_result.get("price_rows_found", 0),
                    "summary_len": len(summary),
                })
                logger.info(
                    "rubika_daily: group=%s prices=%d summary=%d chars",
                    group.group_guid,
                    price_result.get("price_rows_found", 0),
                    len(summary),
                )
            except Exception as e:
                logger.exception("rubika_daily failed for group %s: %s", group.group_guid, e)
                results.append({"group_guid": group.group_guid, "error": str(e)})
        return {"groups_processed": len(results), "results": results}
    except Exception as exc:
        logger.exception("rubika_daily_ai_analysis_task failed: %s", exc)
        return {"error": str(exc)}
    finally:
        session.close()
