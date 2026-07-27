"""Bale User Account connector — ارسال مستقیم به شماره موبایل با aiobale."""

from __future__ import annotations

import logging
import pathlib
import random
import tempfile
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from core_engine.models import Contact, SessionType
from core_engine.services.bale_user_session import parse_session_envelope
from workers.bale_account_pool import BaleAccountPoolManager, resolve_current_bale_window
from workers.config import WorkerSettings
from workers.db import get_db_session
from workers.errors import PermanentWorkerError, SessionInvalidError
from workers.payloads import WorkerPayload, WorkerResult
from workers.session_access import load_account_session_plaintext

if TYPE_CHECKING:
    pass

logger = logging.getLogger("workers.connectors.bale_user")


async def _load_session_path(account_id: int, db: Session) -> str:
    """لود session file از DB و ذخیره موقت."""
    try:
        plaintext = load_account_session_plaintext(
            db,
            account_id=account_id,
            session_type=SessionType.BALE_SESSION,
        )
    except Exception as exc:
        raise SessionInvalidError(f"Bale session not found for account {account_id}: {exc}") from exc

    envelope = parse_session_envelope(plaintext)
    session_bytes = bytes.fromhex(envelope["session_file_bytes"])

    tmp = tempfile.NamedTemporaryFile(suffix=".bale", delete=False)
    tmp.write(session_bytes)
    tmp.close()
    return tmp.name


async def _resolve_peer(client, phone: str):
    """پیدا کردن peer_id از شماره موبایل."""
    phone_clean = phone.strip().lstrip("+")
    if not phone_clean.isdigit():
        raise PermanentWorkerError(f"Invalid phone number format: {phone}")

    contacts = await client.import_contacts(
        contacts=[(int(phone_clean), phone_clean)]
    )

    if contacts and len(contacts) > 0:
        peer = contacts[0]
        if hasattr(peer, "id") and peer.id:
            return peer.id, None

    raise PermanentWorkerError(
        f"Phone ending {phone_clean[-4:]} is not registered on Bale."
    )


async def deliver_bale_user_live(
    payload: WorkerPayload,
    settings: WorkerSettings,
    db: Session | None = None,
) -> WorkerResult:
    """ارسال از طریق اکانت شخصی بله (aiobale)."""
    from aiobale import Client
    from aiobale.enums import ChatType
    from core_engine.services.redis_client import get_redis_client
    from workers.rate_limit import record_successful_send, set_min_delay

    owns_session = db is None
    session = db or get_db_session()
    session_path = None

    try:
        # ۱) بررسی بازه زمانی
        if not resolve_current_bale_window(session):
            return WorkerResult(
                success=False,
                status="failed_retryable",
                error_code="bale_user_outside_send_window",
                error_message="خارج از بازه زمانی مجاز ارسال بله.",
                retryable=True,
            )

        # ۲) انتخاب اکانت
        redis = get_redis_client()
        pool = BaleAccountPoolManager(session)
        account = await pool.get_available_account(
            redis=redis,
            hourly_cap=settings.BALE_HOURLY_SEND_CAP,
        )
        if account is None:
            return WorkerResult(
                success=False,
                status="failed_retryable",
                error_code="bale_user_no_account_available",
                error_message="هیچ اکانت سالمی در pool بله آماده نیست.",
                retryable=True,
            )

        # ۳) لود contact
        try:
            contact_id = int(payload.contact_id)
            contact = session.query(Contact).filter(Contact.id == contact_id).first()
        except (TypeError, ValueError):
            contact = Contact(
                phone_e164=payload.recipient,
                phone=payload.recipient,
                first_name="",
            )
            contact_id = None

        if contact is None:
            return WorkerResult(
                success=False,
                status="failed_permanent",
                error_code="bale_user_contact_missing",
                error_message=f"Contact {payload.contact_id} not found.",
                retryable=False,
            )

        phone = (contact.phone_e164 or contact.phone or payload.recipient or "").strip()
        if not phone:
            return WorkerResult(
                success=False,
                status="failed_permanent",
                error_code="bale_user_no_phone",
                error_message="Contact has no phone number.",
                retryable=False,
            )

        # ۴) لود session path
        try:
            session_path = await _load_session_path(account.id, session)
        except SessionInvalidError as exc:
            pool.mark_account_failed(account_id=account.id, error_message=str(exc))
            return WorkerResult(
                success=False,
                status="failed_permanent",
                error_code="bale_user_session_missing",
                error_message=str(exc),
                retryable=False,
            )

        # ۵) ارسال با async with
        try:
            async with Client(session_file=session_path) as client:
                try:
                    user_id, access_hash = await _resolve_peer(client, phone)
                except PermanentWorkerError as exc:
                    return WorkerResult(
                        success=False,
                        status="failed_permanent",
                        error_code="bale_user_phone_not_found",
                        error_message=str(exc),
                        retryable=False,
                    )

                logger.info(
                    "bale_user_send_attempt account_id=%s contact_id=%s user_id=%s",
                    account.id, contact_id, user_id,
                )

                result = await client.send_message(
                    text=payload.message_text,
                    chat_id=user_id,
                    chat_type=ChatType.PRIVATE,
                )
                message_id = str(getattr(result, 'message_id', '') or user_id)

        except Exception as exc:
            err = str(exc).lower()
            if any(k in err for k in ("auth", "unauthorized", "invalid token", "session")):
                pool.mark_account_failed(account_id=account.id, error_message=str(exc), permanent=False)
                return WorkerResult(
                    success=False,
                    status="failed_permanent",
                    error_code="bale_user_session_invalid",
                    error_message=str(exc),
                    retryable=False,
                )
            if any(k in err for k in ("flood", "rate", "too many")):
                pool.mark_account_failed(account_id=account.id, error_message=str(exc))
                return WorkerResult(
                    success=False,
                    status="failed_retryable",
                    error_code="bale_user_rate_limited",
                    error_message=str(exc),
                    retryable=True,
                )
            return WorkerResult(
                success=False,
                status="failed_retryable",
                error_code="bale_user_api_error",
                error_message=str(exc),
                retryable=True,
            )
        finally:
            if session_path:
                try:
                    pathlib.Path(session_path).unlink(missing_ok=True)
                except Exception:
                    pass

        # ۶) ثبت موفقیت
        await record_successful_send(redis, account.id)
        pool.mark_account_used(account_id=account.id)
        random_delay = random.uniform(
            settings.BALE_MIN_SEND_DELAY_SECONDS,
            settings.BALE_MAX_SEND_DELAY_SECONDS,
        )
        await set_min_delay(redis, account.id, int(random_delay))
        session.commit()

        return WorkerResult(
            success=True,
            status="delivered",
            platform_message_id=f"bale-user-{message_id}",
            retryable=False,
        )

    except Exception as exc:
        session.rollback()
        logger.exception("bale_user_unexpected_error contact_id=%s", payload.contact_id)
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="bale_user_unexpected_error",
            error_message=str(exc),
            retryable=True,
        )
    finally:
        if owns_session:
            session.close()
