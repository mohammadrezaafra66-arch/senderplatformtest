"""حلقه پاسخ هوشمند روبیکا — فاز ۷ سند.

اجرا به‌عنوان سرویس مجزا:

    python -m workers.rubika_ai_response_loop

هر ۳۰ ثانیه پیام‌های تحلیل‌نشده (ai_analyzed=False) گروه‌هایی که
conversation_mode_enabled=True دارند را می‌خواند، برای هرکدام
handle_conversation_reply() را صدا می‌زند و اگر متن پاسخی برگشت، آن را با
همان اکانت پایش (phase=listener) به گروه ارسال می‌کند و پیام را
ai_analyzed=True علامت می‌زند.

اکانت ارسال پاسخ باید همان اکانت پایش باشد (phase=listener) — همان اکانتی که
عضو گروه است و پیام‌ها را دریافت کرده؛ هرگز از فاز ارسال (day/night) یا
استاتوس، طبق قانون امنیتی بخش هفت سند.

حلقه اتصال مجدد با backoff نمایی مشابه workers/rubika_group_listener.py است.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from core_engine.models import RubikaAllowedGroup, RubikaGroupMessage
from core_engine.services.rubika_ai_analyzer import handle_conversation_reply
from workers.connectors.rubika_user import _connect_authenticated, load_rubika_user_client
from workers.db import get_db_session
from workers.errors import SessionInvalidError
from workers.rubika_account_pool import RubikaAccountPoolManager

if TYPE_CHECKING:
    import rubpy

logger = logging.getLogger("workers.rubika_ai_response_loop")

POLL_INTERVAL_SECONDS = 30
BATCH_SIZE = 20
MAX_RETRIES = 10


class RubikaAiResponseLoop:
    """یک نمونه = یک اکانت پایش (phase=listener) که پاسخ‌های هوشمند را ارسال می‌کند."""

    def __init__(self) -> None:
        self.client: "rubpy.Client | None" = None
        self.account_id: int | None = None

    def _select_listener_account_id(self) -> int:
        db = get_db_session()
        try:
            pool = RubikaAccountPoolManager(db)
            candidates = pool.list_pool_accounts("listener")
            if not candidates:
                raise RuntimeError(
                    "هیچ اکانت سالمی با phase='listener' در rubika_account_pool نیست. "
                    "از POST /rubika/accounts/{id}/pool با phase=listener اضافه کن."
                )
            return candidates[0].id
        finally:
            db.close()

    async def _process_pending_once(self) -> None:
        """یک دور: پیام‌های تحلیل‌نشدهٔ گروه‌های conversation_mode را پردازش می‌کند."""
        db: Session = get_db_session()
        try:
            rows = (
                db.query(RubikaGroupMessage)
                .join(
                    RubikaAllowedGroup,
                    RubikaAllowedGroup.group_guid == RubikaGroupMessage.group_guid,
                )
                .filter(
                    RubikaGroupMessage.ai_analyzed.is_(False),
                    RubikaAllowedGroup.conversation_mode_enabled.is_(True),
                )
                .order_by(RubikaGroupMessage.received_at.asc())
                .limit(BATCH_SIZE)
                .all()
            )

            for msg in rows:
                # handle_conversation_reply خودش خطاهای AI را می‌گیرد و None برمی‌گرداند؛
                # برای پیام‌هایی که ریپلای به ما نیستند None برمی‌گرداند بدون علامت‌زدن،
                # پس ai_analyzed را این‌جا صریحاً ست می‌کنیم تا دوباره پردازش نشوند.
                reply = await handle_conversation_reply(db, message_id=msg.id)

                if reply:
                    # اگر اتصال قطع باشد این خط استثنا می‌دهد و به حلقهٔ reconnect می‌رسد؛
                    # چون هنوز commit نکرده‌ایم پیام دوباره پس از اتصال مجدد پردازش می‌شود.
                    await self.client.send_message(object_guid=msg.group_guid, text=reply)
                    logger.info(
                        "rubika_ai_loop_reply_sent message_id=%s group_guid=%s len=%s",
                        msg.id,
                        msg.group_guid,
                        len(reply),
                    )

                msg.ai_analyzed = True
                db.commit()
        finally:
            db.close()

    async def _connect_and_run(self) -> None:
        import asyncio

        self.account_id = self._select_listener_account_id()
        logger.info("rubika_ai_loop_starting account_id=%s", self.account_id)

        try:
            self.client = await load_rubika_user_client(self.account_id)
            await _connect_authenticated(self.client)
        except SessionInvalidError as exc:
            db = get_db_session()
            try:
                RubikaAccountPoolManager(db).mark_account_failed(
                    account_id=self.account_id, error_message=str(exc), permanent=False
                )
                db.commit()
            finally:
                db.close()
            raise

        logger.info(
            "rubika_ai_loop_ready account_id=%s — هر %s ثانیه پیام‌ها را بررسی می‌کند",
            self.account_id,
            POLL_INTERVAL_SECONDS,
        )

        while True:
            await self._process_pending_once()
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def start(self) -> None:
        """اجرای حلقه با اتصال مجدد و backoff نمایی.

        اگر اتصال قطع شود به‌صورت خودکار تا MAX_RETRIES بار دوباره تلاش می‌کند؛
        فاصله بین هر تلاش 30 ثانیه × شماره تلاش است (backoff نمایی خطی).
        SessionInvalidError دائمی است و retry نمی‌شود (سشن باید دوباره لاگین شود).
        """
        import asyncio

        retry_count = 0

        while True:
            try:
                await self._connect_and_run()
                # _connect_and_run معمولاً برنمی‌گردد؛ اگر برگشت یعنی خاموشی عادی.
                logger.info("rubika_ai_loop_stopped account_id=%s", self.account_id)
                return
            except SessionInvalidError:
                # سشن نامعتبر است؛ retry بی‌فایده است — مطابق قانون امنیتی سند.
                raise
            except Exception as exc:  # noqa: BLE001 — هر قطعی اتصال باید منجر به تلاش مجدد شود
                retry_count += 1
                if retry_count > MAX_RETRIES:
                    logger.error(
                        "rubika_ai_loop_giveup account_id=%s پس از %s تلاش ناموفق",
                        self.account_id,
                        MAX_RETRIES,
                    )
                    raise
                delay = 30 * retry_count
                logger.warning(
                    "rubika_ai_loop_connection_lost account_id=%s error=%s — "
                    "تلاش مجدد %s/%s پس از %s ثانیه",
                    self.account_id,
                    exc,
                    retry_count,
                    MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)


async def _amain() -> None:
    logging.basicConfig(level=logging.INFO)
    loop = RubikaAiResponseLoop()
    await loop.start()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_amain())
