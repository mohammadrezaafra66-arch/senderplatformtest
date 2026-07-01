"""پایش زنده گروه‌های مجاز روبیکا — فاز ۵ سند.

اجرا به‌عنوان سرویس مجزا (نه از طریق workers.run_forever که صف‌محور و برای
ارسال است؛ این‌جا یک event-loop دائمی gRPC/long-poll خودِ rubpy است):

    python -m workers.rubika_group_listener

مسئولیت‌ها (دقیقاً مطابق نیازمندی‌های ۱۴ تا ۲۰ سند):
- عضویت در گروه‌های ثبت‌شده در RubikaAllowedGroup و ذخیره هر پیام در RubikaGroupMessage.
- تشخیص نوع پیام: text | voice | image | sticker (پردازش واقعی ویس→متن و
  عکس→متن کار فاز ۶ است؛ این‌جا فقط فایل خام دانلود و مسیرش ذخیره می‌شود).
- تشخیص ریپلای به پیام خودِ اکانت پایش (is_reply_to_our_message).
- کلمه کلیدی per-group → پاسخ خودکار از پیش‌تعریف‌شده.
- کلمه قرمز per-group → فقط فلگ می‌شود؛ شمارش/گزارش روزانه کار فاز ۷ است.
- conversation_mode (پاسخ هوشمند AI) فقط علامت می‌زند (ai_analyzed=False)؛
  پاسخ واقعی را rubika_ai_analyzer.py در فاز ۷ تولید می‌کند.

اکانت پایش باید از فاز اختصاصی "listener" در pool بیاید — هرگز از فاز
ارسال (day/night) یا استاتوس، طبق قانون امنیتی بخش هفت سند.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from core_engine.models import AccountStatus, RubikaAllowedGroup, RubikaGroupMessage
from workers.config import get_worker_settings
from workers.connectors.rubika_user import load_rubika_user_client
from workers.db import get_db_session
from workers.errors import SessionInvalidError
from workers.rubika_account_pool import RubikaAccountPoolManager

if TYPE_CHECKING:
    import rubpy
    from rubpy.types import Update

logger = logging.getLogger("workers.rubika_group_listener")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VOICE_DIR = PROJECT_ROOT / "storage" / "voices" / "rubika"
IMAGE_DIR = PROJECT_ROOT / "storage" / "images" / "rubika"


def _resolve_message_type(update: "Update") -> str:
    if update.voice:
        return "voice"
    if update.photo:
        return "image"
    if update.sticker:
        return "sticker"
    return "text"


def _contains_any(text: str | None, keywords: list[str]) -> bool:
    if not text or not keywords:
        return False
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in keywords if kw)


async def _resolve_sender_name(update: "Update") -> str | None:
    try:
        author = await update.get_author()
    except Exception:  # noqa: BLE001 — اطلاعات فرستنده ضروری نیست، نباید پردازش پیام را متوقف کند
        return None
    first = getattr(author, "first_name", None) or ""
    last = getattr(author, "last_name", None) or ""
    name = f"{first} {last}".strip()
    return name or None


async def _download_attachment(update: "Update", *, group_guid: str, kind: str) -> str | None:
    target_dir = VOICE_DIR if kind == "voice" else IMAGE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_group = re.sub(r"[^a-zA-Z0-9_-]", "_", group_guid)[:64]
    message_id = str(getattr(update, "message_id", "") or "unknown")
    extension = "ogg" if kind == "voice" else "jpg"
    file_path = target_dir / f"{safe_group}_{message_id}.{extension}"
    try:
        await update.download(save_as=str(file_path))
        return str(file_path)
    except Exception:  # noqa: BLE001 — دانلود ناموفق نباید کل پیام را گم کند
        logger.exception(
            "rubika_listener_download_failed group_guid=%s kind=%s", group_guid, kind
        )
        return None


async def _is_reply_to_own_message(update: "Update", client_guid: str) -> bool:
    if not update.reply_message_id:
        return False
    try:
        reply_message = await update.get_reply_message()
    except Exception:  # noqa: BLE001
        return False
    return bool(reply_message) and getattr(reply_message, "author_guid", None) == client_guid


class RubikaGroupListener:
    """یک نمونه = یک اکانت پایش (phase=listener) که روی پیام‌های گروه گوش می‌دهد."""

    def __init__(self) -> None:
        self.settings = get_worker_settings()
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

    async def _handle_message(self, update: "Update") -> None:
        if not update.is_group:
            return  # فقط گروه — پیام خصوصی مهم نیست (نیازمندی پایش گروه)

        group_guid = update.object_guid
        if not group_guid:
            return

        db: Session = get_db_session()
        try:
            group = (
                db.query(RubikaAllowedGroup)
                .filter(
                    RubikaAllowedGroup.group_guid == group_guid,
                    RubikaAllowedGroup.is_active.is_(True),
                )
                .first()
            )
            if group is None:
                return  # گروهی که در لیست مجاز نیست — نادیده بگیر

            message_type = _resolve_message_type(update)
            text = update.text if update.is_text else None
            sender_name = await _resolve_sender_name(update)
            is_reply = await _is_reply_to_own_message(update, self.client.guid)

            voice_path = None
            image_path = None
            if message_type == "voice":
                voice_path = await _download_attachment(update, group_guid=group_guid, kind="voice")
            elif message_type == "image":
                image_path = await _download_attachment(update, group_guid=group_guid, kind="image")

            has_red = _contains_any(text, group.red_keywords or [])

            row = RubikaGroupMessage(
                group_guid=group_guid,
                group_name=group.group_name,
                sender_name=sender_name,
                sender_phone=None,  # rubpy GUID را می‌دهد نه شماره؛ بدون add_address_book جداگانه قابل‌استخراج نیست
                message_type=message_type,
                message_text=text,
                voice_file_path=voice_path,
                image_file_path=image_path,
                is_reply_to_our_message=is_reply,
                has_red_keyword=has_red,
                ai_analyzed=False,
            )
            db.add(row)

            if is_reply:
                logger.warning(
                    "rubika_listener_reply_to_us group_guid=%s sender=%s text=%s",
                    group_guid,
                    sender_name,
                    (text or "")[:200],
                )
                # TODO(فاز ۷/UI): اتصال به کانال نوتیف واقعی ادمین — فعلاً ساخت‌یافته لاگ
                # می‌شود و از طریق GET /rubika/groups/{id}/messages قابل دیدن است.

            if has_red:
                logger.warning(
                    "rubika_listener_red_keyword group_guid=%s text=%s", group_guid, (text or "")[:200]
                )

            if text and group.keywords and group.keyword_response:
                if _contains_any(text, group.keywords):
                    try:
                        await update.reply(group.keyword_response)
                    except Exception:  # noqa: BLE001 — ذخیره پیام مهم‌تر از موفقیت پاسخ خودکار است
                        logger.exception(
                            "rubika_listener_keyword_reply_failed group_guid=%s", group_guid
                        )

            db.commit()

            logger.info(
                "rubika_listener_message_saved group_guid=%s type=%s is_reply=%s has_red=%s",
                group_guid,
                message_type,
                is_reply,
                has_red,
            )

        except Exception:  # noqa: BLE001 — یک پیام خراب نباید کل listener را crash کند
            db.rollback()
            logger.exception("rubika_listener_handle_message_failed group_guid=%s", group_guid)
        finally:
            db.close()

    async def start(self) -> None:
        from rubpy import handlers

        self.account_id = self._select_listener_account_id()
        logger.info("rubika_listener_starting account_id=%s", self.account_id)

        try:
            self.client = await load_rubika_user_client(self.account_id)
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

        async def _on_message(update: "Update") -> None:
            await self._handle_message(update)

        self.client.add_handler(_on_message, handlers.MessageUpdates())

        logger.info("rubika_listener_ready account_id=%s — get_updates() را شروع می‌کند", self.account_id)
        await self.client.run()


async def _amain() -> None:
    logging.basicConfig(level=logging.INFO)
    listener = RubikaGroupListener()
    await listener.start()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_amain())
