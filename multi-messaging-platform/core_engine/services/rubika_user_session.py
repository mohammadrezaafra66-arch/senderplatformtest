"""ورود تعاملی روبیکا (RUBIKA_DELIVERY_MODE=user_account) — OTP دو مرحله‌ای.

دو مسئولیت:
۱. envelope — قالب JSON ذخیره‌شده در ChannelSession.ciphertext با
   session_type=SessionType.RUBIKA_SESSION. پنج فیلد دارد چون
   rubpy.sessions.StringSession.insert() مقدار private_key را در نسخه
   فعلی کتابخانه (۷.۳.۵) گم می‌کند، در حالی‌که Client.connect() دقیقاً
   انتظار آن را در ایندکس ۴ لیست session دارد (دیده‌شده در
   rubpy/methods/utilities/connect.py: information[4] = private_key).
   اینجا آن نقص را با ساختن دستیِ لیست session دور می‌زنیم.
۲. جریان دو مرحله‌ای: start_rubika_user_login (ارسال کد / pass_key) و
   verify_rubika_user_login (تأیید کد، ذخیره session، register_device).
   وضعیت موقت بین این دو مرحله در Redis نگه داشته می‌شود (TTL کوتاه).

نکته امنیتی: private_key فقط در همین envelope رمزشده (Fernet، از طریق
session_storage.store_channel_session) نگه داشته می‌شود — هیچ‌وقت در لاگ
یا پاسخ API چاپ نمی‌شود.
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Any

from sqlalchemy.orm import Session

from core_engine.models import Account, AccountStatus, SessionType
from core_engine.services.redis_client import get_redis_client
from core_engine.services.session_storage import store_channel_session

logger = logging.getLogger("core_engine.services.rubika_user_session")

_ENVELOPE_FIELDS = ("phone_number", "auth", "guid", "user_agent", "private_key")
_REGISTRATION_TTL_SECONDS = 600  # ۱۰ دقیقه — کد پیامکی روبیکا زود منقضی می‌شود
_REDIS_KEY_PREFIX = "rubika:user_login:"


class RubikaLoginError(Exception):
    """خطای قابل‌نمایش به کاربر در جریان ورود (پیام فارسی)."""


def _redis_key(registration_token: str) -> str:
    return f"{_REDIS_KEY_PREFIX}{registration_token}"


def build_session_envelope(
    *, phone_number: str, auth: str, guid: str, user_agent: str, private_key: str
) -> str:
    """ساخت پاکت JSON ۵‌عنصری که Client.connect() در rubpy انتظار دارد."""
    return json.dumps(
        {
            "phone_number": phone_number,
            "auth": auth,
            "guid": guid,
            "user_agent": user_agent,
            "private_key": private_key,
        },
        ensure_ascii=False,
    )


def parse_session_envelope(plaintext: bytes | str) -> dict[str, str]:
    text = plaintext.decode("utf-8") if isinstance(plaintext, bytes) else plaintext
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Rubika session envelope must be a JSON object.")
    missing = [f for f in _ENVELOPE_FIELDS if not str(data.get(f) or "").strip()]
    if missing:
        raise ValueError(f"Rubika session envelope missing fields: {missing}")
    return data


def is_rubika_user_account_mode() -> bool:
    from core_engine.config import get_settings

    settings = get_settings()
    return (
        settings.RUBIKA_DELIVERY_MODE.strip().lower() == "user_account"
        and settings.RUBIKA_USER_ACCOUNT_ENABLED
    )


async def start_rubika_user_login(
    *,
    account_id: int,
    phone_number: str | None = None,
    pass_key: str | None = None,
    registration_token: str | None = None,
) -> dict[str, Any]:
    """مرحله ۱ — ارسال کد پیامکی.

    دو روش فراخوانی:
    - شروع تازه: phone_number بده (بدون registration_token).
    - تکمیل pass_key: registration_token (از پاسخ قبلی با stage=pass_key_required)
      + pass_key بده؛ phone_number از حالت ذخیره‌شده در Redis خوانده می‌شود.
    """
    from rubpy import Client
    from rubpy.crypto import Crypto as RubikaCrypto
    from rubpy.sessions import StringSession

    redis = get_redis_client()

    if registration_token:
        raw_state = await redis.get(_redis_key(registration_token))
        if not raw_state:
            raise RubikaLoginError(
                "registration_token نامعتبر یا منقضی‌شده است — از ابتدا با phone_number شروع کنید."
            )
        state = json.loads(raw_state)
        if state.get("stage") != "pass_key":
            raise RubikaLoginError("این registration_token در مرحله pass_key نیست.")
        phone = str(state["phone_number"])
        if not pass_key or not pass_key.strip():
            raise RubikaLoginError("pass_key نمی‌تواند خالی باشد.")
    else:
        phone = (phone_number or "").strip()
        if not phone:
            raise RubikaLoginError("phone_number نمی‌تواند خالی باشد.")
        if phone.startswith("0"):
            phone = f"98{phone[1:]}"

    client = Client(name=StringSession(), display_welcome=False)
    await client.connect()
    try:
        result = await client.send_code(
            phone_number=phone, pass_key=pass_key, send_type="SMS"
        )
    finally:
        await client.disconnect()

    status = str(getattr(result, "status", "") or "")
    logger.info(
        "rubika_send_code account_id=%s status=%s phone_suffix=%s",
        account_id,
        status,
        phone[-4:] if len(phone) >= 4 else "****",
    )

    if status == "SendPassKey":
        new_token = secrets.token_urlsafe(24)
        state = {"account_id": account_id, "phone_number": phone, "stage": "pass_key"}
        await redis.set(_redis_key(new_token), json.dumps(state), ex=_REGISTRATION_TTL_SECONDS)
        return {
            "registration_token": new_token,
            "stage": "pass_key_required",
            "hint_pass_key": str(getattr(result, "hint_pass_key", "") or ""),
            "message": "این اکانت برای ورود نیاز به رمز اضافه (pass_key) دارد.",
        }

    phone_code_hash = str(getattr(result, "phone_code_hash", "") or "")
    if not phone_code_hash:
        raise RubikaLoginError(
            f"روبیکا phone_code_hash برنگرداند (status={status or 'نامشخص'})."
        )

    public_key, private_key = RubikaCrypto.create_keys()

    new_token = secrets.token_urlsafe(24)
    state = {
        "account_id": account_id,
        "phone_number": phone,
        "phone_code_hash": phone_code_hash,
        "public_key": public_key,
        "private_key": private_key,
        "stage": "code",
    }
    await redis.set(_redis_key(new_token), json.dumps(state), ex=_REGISTRATION_TTL_SECONDS)

    return {
        "registration_token": new_token,
        "stage": "code_required",
        "message": "کد پیامکی ارسال شد.",
    }


async def verify_rubika_user_login(
    db: Session, *, registration_token: str, phone_code: str
) -> dict[str, Any]:
    """مرحله ۲ — تأیید کد، ذخیره session رمزنگاری‌شده، register_device."""
    from rubpy import Client
    from rubpy.crypto import Crypto as RubikaCrypto
    from rubpy.sessions import StringSession

    redis = get_redis_client()
    raw_state = await redis.get(_redis_key(registration_token))
    if not raw_state:
        raise RubikaLoginError(
            "registration_token نامعتبر یا منقضی‌شده است — از ابتدا شروع کنید."
        )

    state = json.loads(raw_state)
    if state.get("stage") != "code":
        raise RubikaLoginError("این registration_token در مرحله تأیید کد نیست.")

    account_id = int(state["account_id"])
    phone_number = str(state["phone_number"])
    phone_code_hash = str(state["phone_code_hash"])
    public_key = str(state["public_key"])
    private_key = str(state["private_key"])

    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise RubikaLoginError(f"اکانت {account_id} پیدا نشد.")

    client = Client(name=StringSession(), private_key=private_key, display_welcome=False)
    await client.connect()
    try:
        result = await client.sign_in(
            phone_code=phone_code.strip(),
            phone_number=phone_number,
            phone_code_hash=phone_code_hash,
            public_key=public_key,
        )
        status = str(getattr(result, "status", "") or "")
        if status != "OK":
            raise RubikaLoginError(
                f"ورود ناموفق بود (status={status or 'نامشخص'}) — کد را دوباره بررسی کنید."
            )

        # دقیقاً همان دنباله‌ای که rubpy/methods/utilities/start.py پس از sign_in موفق
        # روی self انجام می‌دهد — تا register_device با auth/import_key درست امضا شود.
        from Crypto.PublicKey import RSA
        from Crypto.Signature import pkcs1_15

        client.auth = RubikaCrypto.decrypt_RSA_OAEP(private_key, result.auth)
        client.key = RubikaCrypto.passphrase(client.auth)
        client.decode_auth = RubikaCrypto.decode_auth(client.auth)
        client.import_key = pkcs1_15.new(RSA.import_key(private_key.encode()))
        client.guid = str(result.user.user_guid)
        registered_phone = str(getattr(result.user, "phone", "") or phone_number)

        # session.insert() استاندارد رابی‌پای private_key را نگه نمی‌دارد — دستی پر می‌کنیم
        # تا connect() بعدی (information[4]) آن را پیدا کند.
        client.session.session = [
            registered_phone,
            client.auth,
            client.guid,
            client.user_agent,
            private_key,
        ]

        device_label = account.phone_number or f"account-{account_id}"
        await client.register_device(device_model=f"Afrakala-Sender-{device_label}")

        envelope = build_session_envelope(
            phone_number=registered_phone,
            auth=client.auth,
            guid=client.guid,
            user_agent=client.user_agent,
            private_key=private_key,
        )
    finally:
        await client.disconnect()

    store_channel_session(
        db,
        account_id=account_id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=envelope,
    )
    if account.status == AccountStatus.REQUIRES_LOGIN:
        account.status = AccountStatus.ACTIVE
    db.flush()

    await redis.delete(_redis_key(registration_token))

    logger.info("rubika_user_login_success account_id=%s guid=%s", account_id, client.guid)

    return {
        "success": True,
        "account_id": account_id,
        "guid": client.guid,
        "phone_number": registered_phone,
        "message": "session روبیکا با موفقیت ثبت شد.",
    }
