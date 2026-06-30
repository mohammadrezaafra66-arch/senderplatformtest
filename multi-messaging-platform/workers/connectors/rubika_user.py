"""Rubika *user account* connector (rubpy, غیررسمی) — برای RUBIKA_DELIVERY_MODE=user_account.

مسیر bot_api موجود (workers/connectors/rubika.py) دست‌نخورده باقی می‌ماند؛ این فایل
کاملاً مستقل است.

نکته مهم درباره rubpy.exceptions (تست و تأیید شده دستی):
ماژول rubpy.exceptions در زمان import خودش را با یک ExcetionsHandler با __getattr__
پویا جایگزین می‌کند که نام موردنظر را snake_case فرض می‌کند (مثلاً not_registered)
و به PascalCase تبدیل می‌کند تا در globals() پیدا کند. دسترسی مستقیم PascalCase
(exceptions.NotRegistered) به‌خاطر رفتار str.title() روی رشته‌های بدون underscore
**بی‌صدا کلاس پایه ClientError را برمی‌گرداند، نه کلاس واقعی** — یعنی except
exceptions.NotRegistered هرگز exception واقعی را نمی‌گیرد. همیشه snake_case.
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from core_engine.models import Contact, SessionType
from core_engine.services.rubika_user_session import parse_session_envelope
from workers.config import WorkerSettings
from workers.db import get_db_session
from workers.errors import PermanentWorkerError, RetryableWorkerError, SessionInvalidError
from workers.payloads import WorkerPayload, WorkerResult
from workers.rubika_account_pool import RubikaAccountPoolManager, resolve_current_phase
from workers.session_access import load_account_session_plaintext

if TYPE_CHECKING:
    import rubpy

logger = logging.getLogger("workers.connectors.rubika_user")

# دسترسی snake_case اجباری — دلیل در docstring بالای فایل
from rubpy import exceptions as _rubpy_exceptions  # noqa: E402

RubikaNotRegistered = _rubpy_exceptions.not_registered
RubikaInvalidAuth = _rubpy_exceptions.invalid_auth
RubikaTooRequests = _rubpy_exceptions.too_requests
RubikaRequestError = _rubpy_exceptions.request_error


async def load_rubika_user_client(account_id: int, db: Session | None = None) -> "rubpy.Client":
    """ساخت یک Client متصل‌نشده از envelope رمزگشایی‌شده در channel_sessions."""
    from rubpy import Client
    from rubpy.sessions import StringSession

    owns_session = db is None
    session = db or get_db_session()
    try:
        plaintext = load_account_session_plaintext(
            session,
            account_id=account_id,
            session_type=SessionType.RUBIKA_SESSION,
        )
    finally:
        if owns_session:
            session.close()

    envelope = parse_session_envelope(plaintext)

    string_session = StringSession()
    # دستی پر می‌کنیم (نه session.insert) چون insert استاندارد rubpy ۷.۳.۵
    # private_key را گم می‌کند — جزئیات در core_engine/services/rubika_user_session.py
    string_session.session = [
        envelope["phone_number"],
        envelope["auth"],
        envelope["guid"],
        envelope["user_agent"],
        envelope["private_key"],
    ]
    return Client(name=string_session, display_welcome=False)


async def _connect_authenticated(client: "rubpy.Client") -> None:
    """connect() در rubpy فقط auth/guid/private_key را از session می‌خواند —

    import_key و decode_auth را ست نمی‌کند (آن دو فقط داخل start() ست می‌شوند،
    که برای سشن از قبل لاگین‌شده صدا نمی‌زنیم چون start() مسیر ثبت‌نام
    تعاملی/phone+OTP را هم در بر دارد). بدون import_key، هر درخواست امضادار
    (add_address_book، send_message، ...) با
    AttributeError: 'NoneType' object has no attribute 'sign' fail می‌شود
    (در rubpy/network.py: Crypto.sign(self.client.import_key, ...)).
    این تابع همان دو خط را که start() بعد از ورود موفق روی self انجام می‌دهد،
    اینجا برای یک Client از قبل احراز‌شده تکرار می‌کند.
    """
    from Crypto.PublicKey import RSA
    from Crypto.Signature import pkcs1_15
    from rubpy.crypto import Crypto as RubikaCrypto

    await client.connect()
    client.decode_auth = RubikaCrypto.decode_auth(client.auth) if client.auth else None
    client.import_key = (
        pkcs1_15.new(RSA.import_key(client.private_key.encode()))
        if client.private_key
        else None
    )


async def _resolve_object_guid(
    client: "rubpy.Client",
    db: Session,
    *,
    contact: Contact,
) -> str:
    """guid مقصد را برگردان — اول از کش contact.extra_variables، وگرنه add_address_book."""
    cached = (contact.extra_variables or {}).get("rubika_guid")
    if cached:
        return str(cached)

    phone = (contact.phone_e164 or contact.phone or "").strip()
    if not phone:
        raise PermanentWorkerError("Contact has no phone number for Rubika lookup.")

    first_name = (contact.first_name or contact.full_name or "Afrakala").strip() or "Afrakala"
    last_name = (contact.last_name or "").strip()

    result = await client.add_address_book(
        phone=phone, first_name=first_name, last_name=last_name
    )
    guid = str(getattr(result, "user_guid", "") or getattr(result, "guid", "") or "").strip()
    if not guid:
        raise PermanentWorkerError(
            f"Could not resolve a Rubika guid for phone ending {phone[-4:]} "
            "(number may not be registered on Rubika)."
        )

    extra = dict(contact.extra_variables or {})
    extra["rubika_guid"] = guid
    contact.extra_variables = extra
    db.flush()
    return guid


def _check_and_mark_duplicate(db: Session, *, contact_id: int, campaign_id: int | None) -> bool:
    """True یعنی قبلاً به این contact از حالت user_account پیام رفته — رد کن.

    اگر تکراری نبود، بلافاصله ردیف را ثبت می‌کند (قبل از ارسال واقعی) تا دو
    worker هم‌زمان رقابتی روی یک contact، هر دو پیام نفرستند — رزرو خوش‌بینانه.
    در صورت تداخل (IntegrityError) یعنی worker دیگری همین الان رزرو کرد → تکراری.
    """
    from sqlalchemy.exc import IntegrityError

    from core_engine.models import RubikaGlobalSentRegistry

    existing = (
        db.query(RubikaGlobalSentRegistry)
        .filter(RubikaGlobalSentRegistry.contact_id == contact_id)
        .first()
    )
    if existing is not None:
        return True

    row = RubikaGlobalSentRegistry(
        contact_id=contact_id,
        send_count=1,
        last_sent_campaign_id=campaign_id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return True
    return False


async def deliver_rubika_user_live(
    payload: WorkerPayload,
    settings: WorkerSettings,
    db: Session | None = None,
) -> WorkerResult:
    """ارسال از طریق اکانت شخصی روبیکا (rubpy) — استخر چند اکانتی + dedup + عکس."""
    from core_engine.services.redis_client import get_redis_client
    from workers.rate_limit import record_successful_send, set_min_delay

    owns_session = db is None
    session = db or get_db_session()
    try:
        contact_id = int(payload.contact_id)
        campaign_id_int: int | None
        try:
            campaign_id_int = int(payload.campaign_id)
        except (TypeError, ValueError):
            campaign_id_int = None

        # ۱) dedup سراسری — فقط مخصوص user_account (ریسک بن اکانت شخصی)
        if _check_and_mark_duplicate(session, contact_id=contact_id, campaign_id=campaign_id_int):
            return WorkerResult(
                success=True,
                status="skipped_duplicate",
                error_code=None,
                retryable=False,
            )

        # ۲) فاز فعال همین لحظه (به وقت ایران) — خارج از بازه یعنی صبر کن
        phase = resolve_current_phase(session)
        if phase is None:
            return WorkerResult(
                success=False,
                status="failed_retryable",
                error_code="rubika_user_outside_send_window",
                error_message="هیچ بازه فعالی در rubika_sender_schedules ساعت جاری را پوشش نمی‌دهد.",
                retryable=True,
            )

        # ۳) اکانت سالم این فاز که در cooldown/سقف ساعتی نیست
        redis = get_redis_client()
        pool = RubikaAccountPoolManager(session)
        account = await pool.get_available_account(
            phase=phase, redis=redis, hourly_cap=settings.RUBIKA_HOURLY_SEND_CAP
        )
        if account is None:
            return WorkerResult(
                success=False,
                status="failed_retryable",
                error_code="rubika_user_no_account_available",
                error_message=f"هیچ اکانت سالمی در فاز '{phase}' آماده نیست (cooldown یا سقف ساعتی).",
                retryable=True,
            )

        contact = session.query(Contact).filter(Contact.id == contact_id).first()
        if contact is None:
            return WorkerResult(
                success=False,
                status="failed_permanent",
                error_code="rubika_user_contact_missing",
                error_message=f"Contact {contact_id} not found.",
                retryable=False,
            )

        try:
            client = await load_rubika_user_client(account.id, db=session)
        except SessionInvalidError as exc:
            pool.mark_account_failed(
                account_id=account.id, error_message=str(exc), permanent=False
            )
            return WorkerResult(
                success=False,
                status="failed_permanent",
                error_code="rubika_user_session_missing",
                error_message=str(exc),
                retryable=False,
            )

        await _connect_authenticated(client)
        try:
            try:
                guid = await _resolve_object_guid(client, session, contact=contact)
            except PermanentWorkerError as exc:
                return WorkerResult(
                    success=False,
                    status="failed_permanent",
                    error_code="rubika_user_phone_not_resolved",
                    error_message=str(exc),
                    retryable=False,
                )

            logger.info(
                "rubika_user_send_attempt account_id=%s phase=%s contact_id=%s has_media=%s",
                account.id,
                phase,
                contact_id,
                bool(payload.media_url),
            )

            if payload.media_url:
                result = await client.send_photo(
                    object_guid=guid,
                    photo=payload.media_url,
                    caption=payload.message_text,
                )
            else:
                result = await client.send_message(
                    object_guid=guid,
                    text=payload.message_text,
                )

            message_id = str(getattr(result, "message_id", "") or "").strip()

        except RubikaNotRegistered as exc:
            pool.mark_account_failed(
                account_id=account.id, error_message=str(exc), permanent=False
            )
            return WorkerResult(
                success=False,
                status="failed_permanent",
                error_code="rubika_user_session_invalid",
                error_message=str(exc),
                retryable=False,
            )
        except RubikaInvalidAuth as exc:
            pool.mark_account_failed(
                account_id=account.id, error_message=str(exc), permanent=False
            )
            return WorkerResult(
                success=False,
                status="failed_permanent",
                error_code="rubika_user_session_invalid",
                error_message=str(exc),
                retryable=False,
            )
        except RubikaTooRequests as exc:
            pool.mark_account_failed(
                account_id=account.id, error_message=str(exc), permanent=False
            )
            return WorkerResult(
                success=False,
                status="failed_retryable",
                error_code="rubika_user_rate_limited",
                error_message=str(exc),
                retryable=True,
            )
        except RubikaRequestError as exc:
            return WorkerResult(
                success=False,
                status="failed_retryable",
                error_code="rubika_user_api_error",
                error_message=str(exc),
                retryable=True,
            )
        finally:
            await client.disconnect()

        # ۴) موفق — rate limit، dedup را نهایی کن، delay تصادفی بعدی را ثبت کن
        await record_successful_send(redis, account.id)
        pool.mark_account_used(account_id=account.id)
        random_delay = random.uniform(
            settings.RUBIKA_MIN_SEND_DELAY_SECONDS, settings.RUBIKA_MAX_SEND_DELAY_SECONDS
        )
        await set_min_delay(redis, account.id, int(random_delay))
        session.commit()

        return WorkerResult(
            success=True,
            status="delivered",
            platform_message_id=f"rubika-user-{message_id}" if message_id else "rubika-user-sent",
            retryable=False,
        )

    except Exception as exc:  # noqa: BLE001 — آخرین خط دفاعی، باید WorkerResult برگردد نه crash
        session.rollback()
        logger.exception("rubika_user_unexpected_error contact_id=%s", payload.contact_id)
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="rubika_user_unexpected_error",
            error_message=str(exc),
            retryable=True,
        )
    finally:
        if owns_session:
            session.close()
