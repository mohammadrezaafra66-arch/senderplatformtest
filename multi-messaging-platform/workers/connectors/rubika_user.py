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


def _deep_find(data: Any, key: str) -> Any:
    """جستجوی recursive درست در پاسخ rubpy — برای دور زدن باگ واقعی کتابخانه.

    rubpy.types.Update.__getattr__ → find_keys فقط روی *اولین* فرزند dict سطح
    بالا recurse می‌کند و همان نتیجه را برمی‌گرداند، حتی اگر None باشد، بدون
    امتحان فرزندهای بعدی. تست تجربی شد: پاسخ واقعی addAddressBook ساختار
    {"chat_update": {...بدون user_guid...}, "user": {"user_guid": "..."}}
    دارد؛ result.user_guid چون اول وارد شاخه chat_update می‌شود None برمی‌گرداند
    در حالی‌که user_guid واقعاً زیر "user" هست. این تابع همه شاخه‌ها را
    می‌گردد، نه فقط اولی.
    """
    if hasattr(data, "to_dict"):
        data = data.to_dict
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = _deep_find(value, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _deep_find(item, key)
            if found is not None:
                return found
    return None


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
    """guid مقصد را برگردان — اول از کش contact.extra_variables، وگرنه add_address_book.

    نکته: از _deep_find استفاده می‌شود، نه result.user_guid مستقیم — چون پاسخ واقعی
    addAddressBook ساختار {"chat_update": {...}, "user": {"user_guid": "..."}} دارد
    و دسترسی تک‌سطحی به‌خاطر باگ find_keys کتابخانه None برمی‌گرداند (تست شده دستی
    با اکانت واقعی، خرداد ۱۴۰۵).
    """
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

    user_exists = _deep_find(result, "user_exist")
    if user_exists is False:
        raise PermanentWorkerError(
            f"Phone ending {phone[-4:]} is not registered on Rubika (user_exist=false)."
        )

    guid = str(_deep_find(result, "user_guid") or "").strip()
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


async def deliver_rubika_user_live(
    payload: WorkerPayload,
    settings: WorkerSettings,
    db: Session | None = None,
) -> WorkerResult:
    """ارسال از طریق اکانت شخصی روبیکا (rubpy).

    Phase 2 account selection precedence:
    - WorkerPayload.account_id is the EXACT assigned sender (campaign/ops/queue).
    - Pool membership / send-window / cooldown / hourly cap are enforced via
      central preflight — the connector does NOT silently replace the account.
    """
    from core_engine.models import Account
    from core_engine.services.redis_client import get_redis_client
    from core_engine.services.rubika_mode import RUBIKA_MODE_USER_ACCOUNT
    from core_engine.services.rubika_policy import (
        compute_jitter_seconds,
        ensure_warming_started,
        resolve_effective_limits,
        resolve_rubika_lifecycle,
    )
    from core_engine.services.rubika_preflight import (
        ACCOUNT_MISSING,
        REDIS_UNAVAILABLE,
        RubikaPreflightResult,
        evaluate_rubika_send_preflight,
        log_rubika_preflight_denial,
        preflight_to_worker_result,
    )
    from core_engine.services.rubika_quota import (
        clear_failure_count,
        commit_reservation,
        record_send_failure,
        release_reservation,
        reserve_send_quota,
    )

    owns_session = db is None
    session = db or get_db_session()
    reservation = None
    redis = None
    try:
        try:
            contact_id = int(payload.contact_id)
        except (TypeError, ValueError):
            contact_id = None

        try:
            account_id = int(payload.account_id)
        except (TypeError, ValueError):
            denied = RubikaPreflightResult(
                allowed=False,
                code=ACCOUNT_MISSING,
                message="account_id نامعتبر است.",
                retryable=False,
            )
            log_rubika_preflight_denial(
                denied,
                context="user_account",
                campaign_id=payload.campaign_id,
                message_id=payload.message_id,
                recipient_type=payload.recipient_type,
            )
            return preflight_to_worker_result(denied)

        redis = get_redis_client()
        preflight = await evaluate_rubika_send_preflight(
            session,
            account_id=account_id,
            delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
            user_account_enabled=settings.RUBIKA_USER_ACCOUNT_ENABLED,
            campaign_id=payload.campaign_id,
            context="worker",
            redis=redis,
            hourly_cap=settings.RUBIKA_HOURLY_SEND_CAP,
            daily_cap=settings.RUBIKA_DAILY_SEND_CAP,
            check_runtime_limits=True,
            check_pool_membership=True,
            check_send_window=True,
            check_campaign_assignment=True,
        )
        if not preflight.allowed:
            log_rubika_preflight_denial(
                preflight,
                context="user_account",
                campaign_id=payload.campaign_id,
                message_id=payload.message_id,
                recipient_type=payload.recipient_type,
            )
            return preflight_to_worker_result(preflight)

        account = session.query(Account).filter(Account.id == account_id).first()
        assert account is not None  # preflight already verified

        pool = RubikaAccountPoolManager(session)
        phase = resolve_current_phase(session)

        # Phase 3: atomic quota reservation BEFORE transport (burst-safe).
        policy = preflight.details.get("policy") or {}
        daily_cap = int(policy.get("daily_cap") or settings.RUBIKA_DAILY_SEND_CAP)
        hourly_cap = int(policy.get("hourly_cap") or settings.RUBIKA_HOURLY_SEND_CAP)
        try:
            reserve = await reserve_send_quota(
                redis,
                account.id,
                daily_cap=daily_cap,
                hourly_cap=hourly_cap,
                reserve_ttl_seconds=int(settings.RUBIKA_RESERVE_TTL_SECONDS),
            )
        except Exception as exc:  # noqa: BLE001 — fail closed
            denied = RubikaPreflightResult(
                allowed=False,
                code=REDIS_UNAVAILABLE,
                message="سرویس Redis در دسترس نیست؛ ارسال متوقف شد.",
                account_id=account.id,
                delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
                retryable=True,
                details={"error": type(exc).__name__},
            )
            log_rubika_preflight_denial(
                denied,
                context="user_account_reserve",
                campaign_id=payload.campaign_id,
                message_id=payload.message_id,
            )
            return preflight_to_worker_result(denied)

        if not reserve.ok or reserve.reservation is None:
            denied = RubikaPreflightResult(
                allowed=False,
                code=reserve.code,
                message=reserve.code,
                account_id=account.id,
                delivery_mode=RUBIKA_MODE_USER_ACCOUNT,
                retryable=True,
                details={
                    "retry_after_seconds": reserve.retry_after_seconds,
                    "sent_today": reserve.sent_today,
                    "sent_this_hour": reserve.sent_this_hour,
                },
            )
            log_rubika_preflight_denial(
                denied,
                context="user_account_reserve",
                campaign_id=payload.campaign_id,
                message_id=payload.message_id,
            )
            return preflight_to_worker_result(denied)

        reservation = reserve.reservation

        if contact_id is None:
            contact = Contact(
                phone_e164=payload.recipient,
                phone=payload.recipient,
                first_name="",
            )
        else:
            contact = session.query(Contact).filter(Contact.id == contact_id).first()
        if contact is None:
            await release_reservation(redis, reservation)
            reservation = None
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
            await release_reservation(redis, reservation)
            reservation = None
            pool.mark_account_failed(
                account_id=account.id,
                error_message=str(exc),
                requires_relogin=True,
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
                await release_reservation(redis, reservation)
                reservation = None
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

            message_id = str(_deep_find(result, "message_id") or "").strip()

        except RubikaNotRegistered as exc:
            await release_reservation(redis, reservation)
            reservation = None
            pool.mark_account_failed(
                account_id=account.id,
                error_message=str(exc),
                requires_relogin=True,
            )
            return WorkerResult(
                success=False,
                status="failed_permanent",
                error_code="rubika_user_session_invalid",
                error_message=str(exc),
                retryable=False,
            )
        except RubikaInvalidAuth as exc:
            await release_reservation(redis, reservation)
            reservation = None
            pool.mark_account_failed(
                account_id=account.id,
                error_message=str(exc),
                requires_relogin=True,
            )
            return WorkerResult(
                success=False,
                status="failed_permanent",
                error_code="rubika_user_session_invalid",
                error_message=str(exc),
                retryable=False,
            )
        except RubikaTooRequests as exc:
            await release_reservation(redis, reservation)
            reservation = None
            pool.mark_account_failed(
                account_id=account.id, error_message=str(exc), permanent=False
            )
            await record_send_failure(
                redis,
                account.id,
                threshold=int(settings.RUBIKA_FAILURE_THRESHOLD),
                cooldown_seconds=int(settings.RUBIKA_FAILURE_COOLDOWN_SECONDS),
                throttle_seconds=int(settings.RUBIKA_FAILURE_THROTTLE_SECONDS),
            )
            return WorkerResult(
                success=False,
                status="failed_retryable",
                error_code="rubika_user_rate_limited",
                error_message=str(exc),
                retryable=True,
            )
        except RubikaRequestError as exc:
            await release_reservation(redis, reservation)
            reservation = None
            await record_send_failure(
                redis,
                account.id,
                threshold=int(settings.RUBIKA_FAILURE_THRESHOLD),
                cooldown_seconds=int(settings.RUBIKA_FAILURE_COOLDOWN_SECONDS),
                throttle_seconds=int(settings.RUBIKA_FAILURE_THROTTLE_SECONDS),
            )
            return WorkerResult(
                success=False,
                status="failed_retryable",
                error_code="rubika_user_api_error",
                error_message=str(exc),
                retryable=True,
            )
        finally:
            await client.disconnect()

        lifecycle = resolve_rubika_lifecycle(account)
        limits = resolve_effective_limits(
            lifecycle,
            configured_daily_cap=int(settings.RUBIKA_DAILY_SEND_CAP),
            configured_hourly_cap=int(settings.RUBIKA_HOURLY_SEND_CAP),
            configured_min_interval=int(settings.RUBIKA_MIN_SEND_DELAY_SECONDS),
            configured_max_interval=int(settings.RUBIKA_MAX_SEND_DELAY_SECONDS),
            jitter_enabled=bool(settings.RUBIKA_JITTER_ENABLED),
        )
        delay_seconds = compute_jitter_seconds(limits, rng=random)
        await commit_reservation(redis, reservation, min_interval_seconds=delay_seconds)
        reservation = None
        await clear_failure_count(redis, account.id)

        if ensure_warming_started(account):
            logger.info(
                "event=rubika_warming_started account_id=%s",
                account.id,
            )
        pool.mark_account_used(account_id=account.id)
        session.commit()

        return WorkerResult(
            success=True,
            status="delivered",
            platform_message_id=f"rubika-user-{message_id}" if message_id else "rubika-user-sent",
            retryable=False,
        )

    except Exception as exc:  # noqa: BLE001 — آخرین خط دفاعی، باید WorkerResult برگردد نه crash
        session.rollback()
        if reservation is not None and redis is not None:
            try:
                await release_reservation(redis, reservation)
            except Exception:  # noqa: BLE001
                logger.exception("rubika_user_reserve_release_failed")
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
