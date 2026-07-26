"""ورود به بله با شماره موبایل — OTP دو مرحله‌ای با aiobale.

دو مسئولیت:
۱. envelope — قالب JSON ذخیره‌شده در ChannelSession با session_type=BALE_SESSION
۲. جریان دو مرحله‌ای: start_bale_user_login و verify_bale_user_login
   وضعیت موقت بین دو مرحله در Redis نگه داشته می‌شود (TTL=600)
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any

from sqlalchemy.orm import Session

from core_engine.models import Account, AccountStatus, SessionType, BaleAccountPool
from core_engine.services.redis_client import get_redis_client
from core_engine.services.session_storage import store_channel_session

logger = logging.getLogger("core_engine.services.bale_user_session")

_REGISTRATION_TTL_SECONDS = 600
_REDIS_KEY_PREFIX = "bale:user_login:"

# app_id و app_key ثابت aiobale (از source کتابخانه)
_BALE_APP_ID = 30
_BALE_APP_KEY = "liberaPhone"


class BaleLoginError(Exception):
    """خطای قابل‌نمایش به کاربر در جریان ورود."""


def _redis_key(registration_token: str) -> str:
    return f"{_REDIS_KEY_PREFIX}{registration_token}"


def build_session_envelope(
    *, phone_number: str, token: str, user_id: int, session_file_bytes: bytes
) -> str:
    """ساخت پاکت JSON که در ChannelSession ذخیره می‌شود."""
    return json.dumps(
        {
            "phone_number": phone_number,
            "token": token,
            "user_id": user_id,
            "session_file_bytes": session_file_bytes.hex(),
        },
        ensure_ascii=False,
    )


def parse_session_envelope(plaintext: bytes) -> dict[str, Any]:
    """خواندن پاکت JSON از ChannelSession."""
    try:
        data = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid bale session envelope: {exc}") from exc
    for field in ("phone_number", "token", "user_id", "session_file_bytes"):
        if field not in data:
            raise ValueError(f"Missing field in bale session envelope: {field}")
    return data


async def start_bale_user_login(
    account_id: int,
    phone_number: str,
) -> dict[str, Any]:
    """مرحله اول: ارسال کد OTP به شماره موبایل.

    Returns:
        {"status": "code_sent", "registration_token": "...", "phone_number": "..."}
    """
    import hashlib
    import uuid
    from aiobale import Client
    from aiobale.enums import SendCodeType

    redis = get_redis_client()

    phone_clean = phone_number.strip().lstrip("+")
    if not phone_clean.isdigit():
        raise BaleLoginError("شماره موبایل باید فقط عدد باشد.")

    device_hash = hashlib.md5(f"bale-{account_id}-{phone_clean}".encode()).hexdigest()
    device_title = f"SenderPlatform-{account_id}"

    session_name = f"/tmp/bale_setup_{account_id}_{uuid.uuid4().hex[:8]}"
    client = Client(session_file=session_name)

    try:
        response = await client.start_phone_auth(
            phone_number=int(phone_clean),
            app_id=_BALE_APP_ID,
            app_key=_BALE_APP_KEY,
            device_hash=device_hash,
            device_title=device_title,
            send_code_type=SendCodeType.DEFAULT,
        )
    except Exception as exc:
        raise BaleLoginError(f"خطا در ارسال کد بله: {exc}") from exc
    finally:
        await client.stop()

    transaction_hash = response.transaction_hash if hasattr(response, "transaction_hash") else str(response)

    registration_token = secrets.token_urlsafe(24)
    redis_data = json.dumps({
        "account_id": account_id,
        "phone_number": phone_clean,
        "transaction_hash": transaction_hash,
        "device_hash": device_hash,
        "device_title": device_title,
        "session_name": session_name,
    })
    await redis.setex(_redis_key(registration_token), _REGISTRATION_TTL_SECONDS, redis_data)

    logger.info("bale_login_code_sent account_id=%s phone=***%s", account_id, phone_clean[-4:])
    return {
        "status": "code_sent",
        "registration_token": registration_token,
        "phone_number": phone_clean,
    }


async def verify_bale_user_login(
    db: Session,
    registration_token: str,
    code: str,
    account_id: int,
) -> dict[str, Any]:
    """مرحله دوم: تأیید کد OTP و ذخیره session.

    Returns:
        {"status": "logged_in", "user_id": ..., "phone_number": "..."}
    """
    from aiobale import Client

    redis = get_redis_client()
    raw = await redis.get(_redis_key(registration_token))
    if not raw:
        raise BaleLoginError("کد منقضی شده یا نامعتبر است. دوباره درخواست کنید.")

    state = json.loads(raw)
    if state["account_id"] != account_id:
        raise BaleLoginError("توکن برای این اکانت نیست.")

    session_name = state["session_name"]
    transaction_hash = state["transaction_hash"]
    phone_clean = state["phone_number"]

    client = Client(session_file=session_name)

    try:
        result = await client.validate_code(
            transaction_hash=transaction_hash,
            code=code.strip(),
        )
    except Exception as exc:
        await client.stop()
        err = str(exc).lower()
        if "invalid" in err or "wrong" in err or "code" in err:
            raise BaleLoginError("کد وارد شده اشتباه است.") from exc
        raise BaleLoginError(f"خطا در تأیید کد: {exc}") from exc

    try:
        me = await client.get_me()
        user_id = me.id
        token = client.token
    except Exception as exc:
        await client.stop()
        raise BaleLoginError(f"خطا در دریافت اطلاعات کاربر: {exc}") from exc

    # خواندن session file برای ذخیره در DB
    import pathlib
    session_path = pathlib.Path(session_name).with_suffix(".bale")
    try:
        session_bytes = session_path.read_bytes()
    except FileNotFoundError:
        session_path = pathlib.Path(session_name + ".bale")
        session_bytes = session_path.read_bytes()

    await client.stop()

    # ساخت envelope و ذخیره در DB
    envelope = build_session_envelope(
        phone_number=phone_clean,
        token=token or "",
        user_id=user_id,
        session_file_bytes=session_bytes,
    )

    store_channel_session(
        db,
        account_id=account_id,
        session_type=SessionType.BALE_SESSION,
        plaintext=envelope.encode("utf-8"),
    )

    # اضافه کردن به pool اگر نیست
    existing_pool = (
        db.query(BaleAccountPool)
        .filter(BaleAccountPool.account_id == account_id)
        .first()
    )
    if not existing_pool:
        pool_entry = BaleAccountPool(account_id=account_id)
        db.add(pool_entry)

    # فعال کردن اکانت
    account = db.query(Account).filter(Account.id == account_id).first()
    if account and account.status == AccountStatus.RESTING:
        account.status = AccountStatus.ACTIVE

    db.commit()

    # پاک کردن Redis
    await redis.delete(_redis_key(registration_token))

    # پاک کردن فایل موقت
    try:
        session_path.unlink(missing_ok=True)
        pathlib.Path(session_name).unlink(missing_ok=True)
    except Exception:
        pass

    logger.info("bale_login_success account_id=%s user_id=%s", account_id, user_id)
    return {
        "status": "logged_in",
        "user_id": user_id,
        "phone_number": phone_clean,
    }
