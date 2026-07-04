import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta

from core_engine.database import SessionLocal
from core_engine.models import ChannelSession
from core_engine.services.evolution_service import reconnect_instance

logger = logging.getLogger("whatsapp_auto_reconnect")

# backoff schedule (ثانیه)
BACKOFF_SCHEDULE = [5, 15, 45, 90, 180]
MAX_RECONNECT_ATTEMPTS = 5
# yellow_card detection: بیش از این تعداد disconnect در این پنجره زمانی
YELLOW_CARD_THRESHOLD = 4
YELLOW_CARD_WINDOW_MINUTES = 10
CHECK_INTERVAL_SECONDS = 20


def _now():
    return datetime.now(timezone.utc)


def _record_disconnect_event(cs) -> int:
    """timestamp فعلی را به لیست disconnect_events اضافه می‌کند و تعداد در پنجره اخیر را برمی‌گرداند."""
    try:
        events = json.loads(cs.disconnect_events) if cs.disconnect_events else []
    except Exception:
        events = []
    now = _now()
    events.append(now.isoformat())
    # فقط رویدادهای داخل پنجره را نگه دار
    cutoff = now - timedelta(minutes=YELLOW_CARD_WINDOW_MINUTES)
    events = [e for e in events if datetime.fromisoformat(e) >= cutoff]
    cs.disconnect_events = json.dumps(events)
    return len(events)


async def _process_account(cs_id: int) -> None:
    """یک اکانت disconnected را پردازش می‌کند."""
    db = SessionLocal()
    account_id = None
    attempt = 0
    delay = BACKOFF_SCHEDULE[0]
    try:
        cs = db.query(ChannelSession).filter(ChannelSession.id == cs_id).first()
        if cs is None:
            return

        status = (cs.evolution_status or "").strip().lower()

        # اگر connected است، شمارنده را ریست کن
        if status == "connected":
            if (
                cs.reconnect_attempts != 0
                or cs.socket_state != "online"
                or cs.authorization_state != "authorized"
            ):
                cs.reconnect_attempts = 0
                cs.socket_state = "online"
                cs.authorization_state = "authorized"
                cs.updated_at = _now()
                db.commit()
            return

        # اگر blocked است (401 قبلاً تشخیص داده شده)، reconnect نکن
        if cs.authorization_state == "blocked":
            return  # نیاز به QR جدید — auto-reconnect بی‌فایده

        # فقط disconnected را reconnect کن
        if status != "disconnected":
            return

        # socket offline
        cs.socket_state = "offline"

        # تشخیص yellow_card از تعداد disconnect در پنجره اخیر
        recent_count = _record_disconnect_event(cs)
        cs.last_disconnect_at = _now()
        if recent_count >= YELLOW_CARD_THRESHOLD and cs.authorization_state != "blocked":
            cs.authorization_state = "yellow_card"
            logger.warning(
                "auto_reconnect_yellow_card account_id=%s recent_disconnects=%s window=%smin — reducing activity",
                cs.account_id,
                recent_count,
                YELLOW_CARD_WINDOW_MINUTES,
            )

        # بررسی سقف تلاش
        if cs.reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
            logger.error(
                "auto_reconnect_max_attempts account_id=%s attempts=%s — giving up, needs manual intervention",
                cs.account_id,
                cs.reconnect_attempts,
            )
            db.commit()
            return

        # محاسبه backoff بر اساس شماره تلاش
        idx = min(cs.reconnect_attempts, len(BACKOFF_SCHEDULE) - 1)
        delay = BACKOFF_SCHEDULE[idx]

        # آیا به اندازه کافی از آخرین قطعی گذشته؟ (backoff)
        # ساده: فقط تلاش کن، delay بین چرخه‌ها توسط CHECK_INTERVAL کنترل می‌شود
        cs.reconnect_attempts += 1
        cs.updated_at = _now()
        db.commit()

        account_id = cs.account_id
        attempt = cs.reconnect_attempts
    except Exception as exc:
        db.rollback()
        logger.error("auto_reconnect_db_error cs_id=%s err=%s", cs_id, str(exc))
        return
    finally:
        db.close()

    if account_id is None:
        return

    # تلاش reconnect (خارج از session)
    logger.info(
        "auto_reconnect_attempt account_id=%s attempt=%s delay_hint=%ss",
        account_id,
        attempt,
        delay,
    )
    await reconnect_instance(account_id)


async def _loop() -> None:
    logger.info(
        "auto_reconnect_started interval=%ss max_attempts=%s",
        CHECK_INTERVAL_SECONDS,
        MAX_RECONNECT_ATTEMPTS,
    )
    while True:
        try:
            db = SessionLocal()
            try:
                # اکانت‌هایی که disconnected هستند و blocked نیستند
                rows = (
                    db.query(ChannelSession.id)
                    .filter(ChannelSession.evolution_status == "disconnected")
                    .all()
                )
                ids = [r[0] for r in rows]
            finally:
                db.close()

            for cs_id in ids:
                await _process_account(cs_id)
        except Exception as exc:
            logger.error("auto_reconnect_loop_error err=%s", str(exc))
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_loop())


if __name__ == "__main__":
    main()
