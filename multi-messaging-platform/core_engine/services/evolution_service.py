"""سرویس مدیریت Instanceهای Evolution API — یک Instance به ازای هر اکانت."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.database import SessionLocal
from core_engine.models import Account, ChannelSession, SessionType
from core_engine.services.proxy_assignment_service import (
    get_proxy_config_for_instance,
)

logger = logging.getLogger(__name__)


# ─── توابع کمکی داخلی ───────────────────────────────────────────

def _instance_name(account_id: int) -> str:
    """نام یکتای Instance در Evolution API."""
    return f"mmp-whatsapp-{account_id}"


def _evo_headers(api_key: str) -> dict[str, str]:
    return {"apikey": api_key, "Content-Type": "application/json"}


def _get_instance_name(db: "Session", account_id: int) -> str:
    cs = (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == account_id,
            ChannelSession.session_type == SessionType.EVOLUTION_INSTANCE,
        )
        .first()
    )
    if cs and cs.instance_name:
        return cs.instance_name
    return _instance_name(account_id)


def _get_or_create_channel_session(db: Session, account_id: int) -> ChannelSession:
    """ChannelSession از نوع EVOLUTION_INSTANCE را پیدا یا بساز."""
    cs = (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == account_id,
            ChannelSession.session_type == SessionType.EVOLUTION_INSTANCE,
        )
        .first()
    )
    if not cs:
        cs = ChannelSession(
            account_id=account_id,
            session_type=SessionType.EVOLUTION_INSTANCE,
        )
        db.add(cs)
    return cs


# ─── توابع اصلی ─────────────────────────────────────────────────

async def create_or_get_instance(
    account_id: int, db: Session | None = None
) -> dict[str, Any]:
    """
    Instance واتساپ را در Evolution API بساز یا اگر وجود دارد برگردان.
    
    اگر WHATSAPP_EVOLUTION_REQUIRE_PROXY=True باشد و Proxy تخصیص
    نشده باشد، با RuntimeError متوقف می‌شود.
    """
    settings = get_settings()
    base_url = settings.EVOLUTION_API_URL.rstrip("/")
    api_key = settings.EVOLUTION_API_KEY

    owns_session = db is None
    db_session = db or SessionLocal()
    instance = _get_instance_name(db_session, account_id)

    try:
        # گام ۱: بررسی وجود Instance در Evolution
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base_url}/instance/fetchInstances",
                headers=_evo_headers(api_key),
            )

        instances: list[dict] = []
        if resp.status_code == 200:
            data = resp.json()
            # پاسخ ممکن است list مستقیم باشد یا {"instances": [...]}
            instances = data if isinstance(data, list) else data.get("instances", [])

        existing = [i for i in instances if i.get("instanceName") == instance]
        if existing:
            logger.info(
                "evolution_instance_exists account_id=%s instance=%s",
                account_id, instance,
            )
            return existing[0]

        # گام ۲: واکشی proxy اختصاصی این اکانت
        proxy_config = get_proxy_config_for_instance(db_session, account_id)

        if settings.WHATSAPP_EVOLUTION_REQUIRE_PROXY and proxy_config is None:
            raise RuntimeError(
                f"اکانت {account_id} هنوز proxy ثابت ندارد. "
                "ابتدا از طریق /accounts/{id}/proxy/assign یک proxy تخصیص دهید. "
                "یا متغیر WHATSAPP_EVOLUTION_REQUIRE_PROXY=false قرار دهید "
                "(توصیه نمی‌شود)."
            )

        # گام ۳: تعیین Webhook URL (فقط اگر صریحاً ست شده باشد)
        webhook_url = (settings.EVOLUTION_WEBHOOK_URL or "").strip()

        # گام ۴: ساخت payload ایجاد Instance
        payload: dict[str, Any] = {
            "instanceName": instance,
            "integration": "WHATSAPP-BAILEYS",
        }

        # webhook فقط وقتی به payload اضافه می‌شود که EVOLUTION_WEBHOOK_URL ست شده باشد؛
        # در غیر این صورت برخی نسخه‌های Evolution روی URL نامعتبر، ساخت Instance را رد می‌کنند.
        if webhook_url:
            payload["webhook"] = {
                "enabled": True,
                "url": webhook_url,
                "events": [
                    "APPLICATION_STARTUP",
                    "QRCODE_UPDATED",
                    "MESSAGES_SET",
                    "MESSAGES_UPSERT",
                    "SEND_MESSAGE",
                    "CONNECTION_UPDATE",
                ],
            }
        # پسورد proxy هرگز در لاگ نمی‌آید — فقط وجود/نبودش لاگ می‌شود
        if proxy_config:
            payload["proxy"] = proxy_config
        
        logger.info(
            "evolution_creating_instance account_id=%s proxy_assigned=%s",
            account_id, bool(proxy_config),
        )

        # گام ۵: ایجاد Instance در Evolution
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base_url}/instance/create",
                headers=_evo_headers(api_key),
                json=payload,
            )

        if resp.status_code in (200, 201):
            result = resp.json()
        elif resp.status_code == 403 and "already in use" in resp.text.lower():
            # Evolution می‌گوید این Instance از قبل وجود دارد — خطا نیست.
            logger.info(
                "evolution_instance_already_in_use account_id=%s instance=%s",
                account_id, instance,
            )
            result = {"instanceName": instance, "already_in_use": True}
        else:
            raise RuntimeError(
                f"Evolution API خطا داد هنگام ایجاد Instance '{instance}': "
                f"HTTP {resp.status_code} — {resp.text[:300]}"
            )

        # گام ۶: ذخیره‌ی اطلاعات Instance در ChannelSession
        cs = _get_or_create_channel_session(db_session, account_id)
        cs.instance_name = instance
        cs.evolution_status = "created"
        cs.evolution_webhook_url = webhook_url
        cs.updated_at = datetime.now(timezone.utc)
        db_session.commit()

        return result

    except httpx.TimeoutException:
        raise RuntimeError(
            f"تایم‌اوت در اتصال به Evolution API ({base_url})"
        )
    except httpx.ConnectError:
        raise RuntimeError(
            f"عدم اتصال به Evolution API — "
            f"آیا سرویس evolution_api در Docker در حال اجرا است؟"
        )
    finally:
        if owns_session:
            db_session.close()


async def get_instance_qr(account_id: int) -> str | None:
    """QR Code (base64) برای Instance واتساپ بگیر — برای نمایش به کاربر."""
    settings = get_settings()
    base_url = settings.EVOLUTION_API_URL.rstrip("/")
    api_key = settings.EVOLUTION_API_KEY
    _db = SessionLocal()
    try:
        instance = _get_instance_name(_db, account_id)
    finally:
        _db.close()

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{base_url}/instance/connect/{instance}",
                headers=_evo_headers(api_key),
            )

        if resp.status_code != 200:
            logger.warning(
                "evolution_qr_failed account_id=%s http_status=%s",
                account_id, resp.status_code,
            )
            return None

        data = resp.json()
        # Evolution API ممکن است در نسخه‌های مختلف فیلدهای متفاوت برگرداند
        return (
            data.get("base64")
            or data.get("qrcode")
            or data.get("qrCode")
        )

    except (httpx.TimeoutException, httpx.ConnectError):
        logger.warning("evolution_qr_connection_error account_id=%s", account_id)
        return None


async def check_instance_connection(account_id: int, db=None) -> bool:
    """آیا Instance واتساپ این اکانت در حال حاضر متصل است (state == open)؟"""
    settings = get_settings()
    base_url = settings.EVOLUTION_API_URL.rstrip("/")
    api_key = settings.EVOLUTION_API_KEY
    from core_engine.database import SessionLocal as _SL
    _owns = db is None
    _db = db or _SL()
    instance = _get_instance_name(_db, account_id)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{base_url}/instance/connectionState/{instance}",
                headers=_evo_headers(api_key),
            )
        if resp.status_code != 200:
            return False
        _data = resp.json()
        state = _data.get("state") or _data.get("instance", {}).get("state", "")
        return str(state).lower() == "open"

    except (httpx.TimeoutException, httpx.ConnectError):
        logger.warning(
            "evolution_connection_check_error account_id=%s", account_id
        )
        return False


async def logout_instance(
    account_id: int, db: Session | None = None
) -> dict[str, Any]:
    """
    Instance واتساپ را Logout کن.
    مهم: فیلدهای proxy_* در ChannelSession دست‌نخورده می‌مانند.
    Proxy تخصیص‌یافته حذف نمی‌شود — چون اگر دوباره وصل شود باید همان IP را داشته باشد.
    """
    settings = get_settings()
    base_url = settings.EVOLUTION_API_URL.rstrip("/")
    api_key = settings.EVOLUTION_API_KEY

    owns_session = db is None
    db_session = db or SessionLocal()
    instance = _get_instance_name(db_session, account_id)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(
                f"{base_url}/instance/logout/{instance}",
                headers=_evo_headers(api_key),
            )

        success = resp.status_code in (200, 204)

        # به‌روزرسانی وضعیت در DB — فقط فیلدهای evolution_status و disconnected_at
        # هرگز فیلدهای proxy_* را تغییر نده
        cs = (
            db_session.query(ChannelSession)
            .filter(
                ChannelSession.account_id == account_id,
                ChannelSession.session_type == SessionType.EVOLUTION_INSTANCE,
            )
            .first()
        )
        if cs:
            cs.evolution_status = "disconnected"
            cs.disconnected_at = datetime.now(timezone.utc)
            db_session.commit()

        logger.info(
            "evolution_logout account_id=%s success=%s", account_id, success
        )
        return {
            "success": success,
            "account_id": account_id,
            "instance": instance,
            "message": (
                "Instance با موفقیت Logout شد."
                if success
                else f"Logout ناموفق بود — HTTP {resp.status_code}"
            ),
        }

    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        logger.error("evolution_logout_error account_id=%s error=%s", account_id, exc)
        return {
            "success": False,
            "account_id": account_id,
            "instance": instance,
            "message": f"خطا در ارتباط با Evolution API: {exc}",
        }
    finally:
        if owns_session:
            db_session.close()
