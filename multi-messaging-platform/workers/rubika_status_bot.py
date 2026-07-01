"""ربات استاتوس روبیکا (Rubino) — فاز ۸ سند.

اجرا به‌عنوان loop مجزا:
    python -m workers.rubika_status_bot

⚠️  بالاترین ریسک ban اکانت در کل پروژه.
    اکانت status باید phase=status در pool و کاملاً مجزا از ارسال/پایش باشد.
    با یک اکانت تستی کاملاً مجزا آزمایش کن قبل از اکانت اصلی.

سه قابلیت (مطابق نیازمندی‌های ۲۲-۲۴ سند):
    ۱. لایک خودکار استاتوس‌ها — تأخیر تصادفی ۵-۳۰ ثانیه — cap ۳۰/ساعت
    ۲. نظر هوشمند GPT-4o-mini — cap ۱۰ نظر در روز
    ۳. انتشار استاتوس از RubikaContentSchedule در زمان مقرر

API Rubino (از rubpy/rubino/client.py — تأیید شده):
    rubino.get_recent_following_posts(profile_id)   → پست‌های جدید
    rubino.like(post_id, post_profile_id, profile_id)
    rubino.add_comment(text, post_id, post_profile_id, profile_id)
    rubino.add_picture(profile_id, picture_path, caption)

Rate limiting با Redis (همان workers/rate_limit.py):
    کلید لایک:   rubika:status:like:{account_id}
    کلید نظر:    rubika:status:comment:{account_id}:daily
    TTL لایک:    ۳۶۰۰ ثانیه (سقف ۳۰/ساعت)
    TTL نظر:     ۸۶۴۰۰ ثانیه (سقف ۱۰/روز)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from core_engine.models import AccountStatus, RubikaContentSchedule
from workers.config import get_worker_settings
from workers.connectors.rubika_user import load_rubika_user_client
from workers.db import get_db_session
from workers.errors import SessionInvalidError
from workers.rubika_account_pool import RubikaAccountPoolManager

logger = logging.getLogger("workers.rubika_status_bot")

_HOURLY_LIKE_CAP = int(os.getenv("RUBIKA_STATUS_HOURLY_LIKE_CAP", "30"))
_DAILY_COMMENT_CAP = int(os.getenv("RUBIKA_STATUS_DAILY_COMMENT_CAP", "10"))
_LIKE_DELAY_MIN = int(os.getenv("RUBIKA_STATUS_LIKE_DELAY_MIN_SECONDS", "5"))
_LIKE_DELAY_MAX = int(os.getenv("RUBIKA_STATUS_LIKE_DELAY_MAX_SECONDS", "30"))
_POLL_INTERVAL = 120  # هر ۲ دقیقه loop اجرا می‌شود

_COMMENT_SYSTEM = """تو یک دستیار تعاملی فارسی‌زبان هستی که استاتوس‌های روبیکا را می‌بینی.
یک نظر کوتاه (حداکثر ۱۵ کلمه)، طبیعی و مثبت فارسی بنویس که با محتوا تناسب داشته باشد.
فقط متن نظر را بنویس، هیچ توضیحی نده."""


# ─── Redis rate-limiting helpers ─────────────────────────────────────────────


async def _get_like_count(redis, account_id: int) -> int:
    key = f"rubika:status:like:{account_id}"
    val = await redis.get(key)
    return int(val) if val else 0


async def _increment_like(redis, account_id: int) -> None:
    key = f"rubika:status:like:{account_id}"
    pipe = redis.pipeline()
    await pipe.incr(key)
    await pipe.expire(key, 3600)
    await pipe.execute()


async def _get_comment_count(redis, account_id: int) -> int:
    key = f"rubika:status:comment:{account_id}:daily"
    val = await redis.get(key)
    return int(val) if val else 0


async def _increment_comment(redis, account_id: int) -> None:
    key = f"rubika:status:comment:{account_id}:daily"
    pipe = redis.pipeline()
    await pipe.incr(key)
    await pipe.expire(key, 86400)
    await pipe.execute()


# ─── GPT comment generator ───────────────────────────────────────────────────


async def _generate_comment(context: str) -> str | None:
    """نظر کوتاه فارسی با GPT-4o-mini — None اگر API key نداشت."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=50,
            temperature=0.8,
            messages=[
                {"role": "system", "content": _COMMENT_SYSTEM},
                {"role": "user", "content": context[:300]},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or None
    except Exception:
        logger.exception("rubika_status: comment generation failed")
        return None


# ─── main bot class ───────────────────────────────────────────────────────────


class RubikaStatusBot:
    """یک نمونه = یک اکانت status که loop دائمی اجرا می‌کند."""

    def __init__(self) -> None:
        self.settings = get_worker_settings()
        self.client = None
        self.account_id: int | None = None
        self._rubino_profile_id: str | None = None

    def _select_status_account_id(self) -> int:
        db = get_db_session()
        try:
            pool = RubikaAccountPoolManager(db)
            candidates = pool.list_pool_accounts("status")
            if not candidates:
                raise RuntimeError(
                    "هیچ اکانت سالمی با phase='status' در rubika_account_pool نیست."
                )
            return candidates[0].id
        finally:
            db.close()

    async def _ensure_rubino_profile_id(self) -> str:
        """profile_id اکانت را از Rubino API می‌گیرد (lazy, cached)."""
        if self._rubino_profile_id:
            return self._rubino_profile_id
        from rubpy.rubino import Rubino
        async with Rubino(self.client) as rubino:
            info = await rubino.get_my_profile_info()
            # پاسخ: Update با field profile_id یا id
            profile_id = str(
                getattr(info, "profile_id", None)
                or getattr(info, "id", None)
                or ""
            ).strip()
        if not profile_id:
            raise RuntimeError("rubika_status: نتوانستم profile_id را از Rubino بگیرم.")
        self._rubino_profile_id = profile_id
        logger.info("rubika_status: profile_id=%s", profile_id)
        return profile_id

    async def _run_like_and_comment_cycle(self, redis) -> dict:
        """یک دوره: دریافت پست‌های جدید + لایک + نظر (با rate cap)."""
        from rubpy.rubino import Rubino

        like_count = await _get_like_count(redis, self.account_id)
        comment_count = await _get_comment_count(redis, self.account_id)

        if like_count >= _HOURLY_LIKE_CAP:
            logger.info(
                "rubika_status: سقف ساعتی لایک (%d) پر است", _HOURLY_LIKE_CAP
            )
            return {"likes": 0, "comments": 0, "reason": "hourly_cap"}

        profile_id = await self._ensure_rubino_profile_id()
        liked = commented = 0

        async with Rubino(self.client) as rubino:
            try:
                result = await rubino.get_recent_following_posts(profile_id=profile_id)
                posts = getattr(result, "posts", None) or getattr(result, "items", None) or []
            except Exception:
                logger.exception("rubika_status: دریافت پست‌ها ناموفق")
                return {"likes": 0, "comments": 0}

            for post in posts:
                if like_count + liked >= _HOURLY_LIKE_CAP:
                    break

                post_id = str(getattr(post, "post_id", None) or getattr(post, "id", "")).strip()
                post_profile_id = str(
                    getattr(post, "profile_id", None)
                    or getattr(post, "post_profile_id", "")
                ).strip()
                if not post_id or not post_profile_id:
                    continue

                try:
                    await rubino.like(
                        post_id=post_id,
                        post_profile_id=post_profile_id,
                        profile_id=profile_id,
                    )
                    liked += 1
                    await _increment_like(redis, self.account_id)
                    delay = random.uniform(_LIKE_DELAY_MIN, _LIKE_DELAY_MAX)
                    await asyncio.sleep(delay)
                except Exception:
                    logger.exception("rubika_status: لایک پست %s ناموفق", post_id)
                    continue

                # نظر هوشمند اگر هنوز cap نرسیده
                if comment_count + commented < _DAILY_COMMENT_CAP:
                    caption = str(getattr(post, "caption", "") or "").strip()
                    comment_context = caption or "یک پست روبیکا"
                    comment_text = await _generate_comment(comment_context)
                    if comment_text:
                        try:
                            await rubino.add_comment(
                                text=comment_text,
                                post_id=post_id,
                                post_profile_id=post_profile_id,
                                profile_id=profile_id,
                            )
                            commented += 1
                            await _increment_comment(redis, self.account_id)
                            await asyncio.sleep(random.uniform(3, 8))
                        except Exception:
                            logger.exception("rubika_status: نظر پست %s ناموفق", post_id)

        return {"likes": liked, "comments": commented}

    async def _run_publish_cycle(self) -> dict:
        """انتشار استاتوس‌های زمان‌بندی‌شده که وقتشان رسیده."""
        from rubpy.rubino import Rubino

        db: Session = get_db_session()
        published = 0
        try:
            now = datetime.utcnow()
            pending = (
                db.query(RubikaContentSchedule)
                .filter(
                    RubikaContentSchedule.published.is_(False),
                    RubikaContentSchedule.scheduled_at <= now,
                )
                .order_by(RubikaContentSchedule.scheduled_at.asc())
                .limit(5)
                .all()
            )

            if not pending:
                return {"published": 0}

            profile_id = await self._ensure_rubino_profile_id()

            async with Rubino(self.client) as rubino:
                for item in pending:
                    try:
                        if item.content_type in ("Picture", "Video") and item.media_path:
                            if not Path(item.media_path).exists():
                                raise FileNotFoundError(
                                    f"فایل {item.media_path} وجود ندارد"
                                )
                            if item.content_type == "Picture":
                                await rubino.add_picture(
                                    profile_id=profile_id,
                                    picture=item.media_path,
                                    caption=item.caption,
                                )
                            else:
                                await rubino.add_video(
                                    profile_id=profile_id,
                                    video=item.media_path,
                                    caption=item.caption,
                                )
                        # text_only: روبینو متن خالی به‌عنوان استاتوس ندارد — فقط لاگ

                        item.published = True
                        item.published_at = datetime.utcnow()
                        item.error_message = None
                        db.flush()
                        published += 1
                        logger.info("rubika_status: استاتوس #%d منتشر شد", item.id)
                        await asyncio.sleep(random.uniform(5, 15))

                    except Exception as exc:
                        item.error_message = str(exc)[:512]
                        db.flush()
                        logger.exception(
                            "rubika_status: انتشار استاتوس #%d ناموفق", item.id
                        )

            db.commit()
        except Exception:
            db.rollback()
            logger.exception("rubika_status: publish cycle crashed")
        finally:
            db.close()

        return {"published": published}

    async def start(self) -> None:
        from core_engine.services.redis_client import get_redis_client

        self.account_id = self._select_status_account_id()
        logger.info("rubika_status_bot_starting account_id=%s", self.account_id)

        try:
            self.client = await load_rubika_user_client(self.account_id)
        except SessionInvalidError as exc:
            db = get_db_session()
            try:
                RubikaAccountPoolManager(db).mark_account_failed(
                    account_id=self.account_id,
                    error_message=str(exc),
                    permanent=False,
                )
                db.commit()
            finally:
                db.close()
            raise

        await self.client.connect()
        redis = get_redis_client()

        logger.info(
            "rubika_status_bot_ready account_id=%s — loop هر %ds",
            self.account_id,
            _POLL_INTERVAL,
        )

        try:
            while True:
                try:
                    like_result = await self._run_like_and_comment_cycle(redis)
                    publish_result = await self._run_publish_cycle()
                    logger.info(
                        "rubika_status_cycle: likes=%s comments=%s published=%s",
                        like_result.get("likes"),
                        like_result.get("comments"),
                        publish_result.get("published"),
                    )
                except Exception:
                    logger.exception("rubika_status: cycle error — ادامه می‌دهد")

                await asyncio.sleep(_POLL_INTERVAL)
        finally:
            await self.client.disconnect()


async def _amain() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = RubikaStatusBot()
    await bot.start()


if __name__ == "__main__":
    asyncio.run(_amain())
