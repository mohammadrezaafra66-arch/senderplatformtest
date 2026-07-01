"""ò«‰ò Ê— MTProto »—«Ì «—”«· „” ﬁÌ„ »Â ‘„«—Â „Ê»«Ì· »« Telethon."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import FloodWaitError, PeerFloodError, UserPrivacyRestrictedError
from telethon.tl.functions.contacts import ImportContactsRequest
from telethon.tl.types import InputPhoneContact

from workers.config import WorkerSettings
from workers.db import get_db_session
from core_engine.models import SessionType


async def load_mtproto_client(account_id: int, settings: WorkerSettings) -> TelegramClient:
    from core_engine.services.session_storage import load_channel_session_plaintext
    from core_engine.models import ChannelSession
    db = get_db_session()
    try:
        row = (
            db.query(ChannelSession)
            .filter(
                ChannelSession.account_id == account_id,
                ChannelSession.session_type == SessionType.MTPROTO_SESSION,
            )
            .order_by(ChannelSession.id.desc())
            .first()
        )
        if row is None:
            raise RuntimeError(f"No MTPROTO_SESSION found for account {account_id}")
        plaintext = load_channel_session_plaintext(row)
    finally:
        db.close()

    session_dir = Path(settings.TELEGRAM_MTPROTO_SESSION_DIR)
    session_dir.mkdir(parents=True, exist_ok=True)
    session_path = session_dir / f"account_{account_id}.session"

    with open(session_path, "wb") as f:
        f.write(plaintext)

    client = TelegramClient(
        str(session_path),
        int(settings.TELEGRAM_API_ID),
        settings.TELEGRAM_API_HASH,
    )
    await client.connect()

    if not await client.is_user_authorized():
        raise RuntimeError("Telegram MTProto session is not authorized / expired.")

    return client


def _check_account_pool(account_id: int) -> dict:
    from core_engine.models import TelegramAccountPool
    db = get_db_session()
    try:
        pool = db.query(TelegramAccountPool).filter(
            TelegramAccountPool.account_id == account_id
        ).first()
        if pool is None:
            return {"allowed": False, "reason": "account_not_in_pool"}
        if not pool.is_healthy:
            return {"allowed": False, "reason": "account_unhealthy"}
        if pool.sent_today >= pool.daily_cap_today:
            return {"allowed": False, "reason": "daily_cap_reached"}
        return {"allowed": True, "pool_id": pool.id}
    finally:
        db.close()


def _increment_sent_count(account_id: int) -> None:
    from core_engine.models import TelegramAccountPool
    db = get_db_session()
    try:
        pool = db.query(TelegramAccountPool).filter(
            TelegramAccountPool.account_id == account_id
        ).first()
        if pool:
            pool.sent_today += 1
            db.commit()
    finally:
        db.close()


def _mark_account_unhealthy(account_id: int, error: str) -> None:
    from core_engine.models import TelegramAccountPool
    db = get_db_session()
    try:
        pool = db.query(TelegramAccountPool).filter(
            TelegramAccountPool.account_id == account_id
        ).first()
        if pool:
            pool.is_healthy = False
            pool.last_error_at = datetime.utcnow()
            pool.last_error_message = error[:500]
            db.commit()
    finally:
        db.close()


def _register_lead_if_resolved(phone_number: str, telegram_user) -> None:
    from core_engine.models import TelegramMTProtoLead
    db = get_db_session()
    try:
        existing = db.query(TelegramMTProtoLead).filter(
            TelegramMTProtoLead.phone_number == phone_number
        ).first()
        if existing:
            existing.last_activity_at = datetime.utcnow()
        else:
            lead = TelegramMTProtoLead(
                phone_number=phone_number,
                telegram_user_id=str(telegram_user.id) if telegram_user else None,
                username=telegram_user.username if telegram_user else None,
                source="resolved_contact",
            )
            db.add(lead)
        db.commit()
    finally:
        db.close()


async def deliver_telegram_mtproto_live(payload, settings: WorkerSettings):
    from workers.payloads import WorkerResult
    from core_engine.models import TelegramGlobalSentRegistry

    phone_number = str(payload.recipient).strip()
    account_id = int(payload.account_id)

    db = get_db_session()
    try:
        already_sent = db.query(TelegramGlobalSentRegistry).filter(
            TelegramGlobalSentRegistry.phone_number == phone_number
        ).first()
        if already_sent:
            return WorkerResult(
                success=False,
                status="skipped_duplicate",
                error_code="telegram_already_sent",
                error_message=f"Phone {phone_number} already received a message.",
                retryable=False,
            )
    finally:
        db.close()

    pool_check = _check_account_pool(account_id)
    if not pool_check["allowed"]:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code=f"telegram_pool_{pool_check['reason']}",
            error_message=pool_check["reason"],
            retryable=True,
        )

    delay = random.uniform(
        settings.TELEGRAM_MIN_SEND_DELAY_SECONDS,
        settings.TELEGRAM_MAX_SEND_DELAY_SECONDS,
    )
    await asyncio.sleep(delay)

    client = None
    try:
        client = await load_mtproto_client(account_id, settings)

        contact = InputPhoneContact(
            client_id=0,
            phone=phone_number,
            first_name=payload.metadata.get("first_name", "") if hasattr(payload, "metadata") and payload.metadata else "",
            last_name=payload.metadata.get("last_name", "") if hasattr(payload, "metadata") and payload.metadata else "",
        )
        result = await client(ImportContactsRequest([contact]))

        if not result.users:
            return WorkerResult(
                success=False,
                status="failed_permanent",
                error_code="telegram_number_not_on_telegram",
                error_message=f"{phone_number}  ·ê—«„ ‰œ«—œ Ì« ﬁ«»· ÅÌœ« ò—œ‰ ‰Ì” .",
                retryable=False,
            )

        telegram_user = result.users[0]
        _register_lead_if_resolved(phone_number, telegram_user)

        sent_message = await client.send_message(telegram_user, payload.message_text)

        db = get_db_session()
        try:
            registry_entry = TelegramGlobalSentRegistry(
                phone_number=phone_number,
                last_sent_campaign_id=int(payload.campaign_id) if payload.campaign_id else None,
            )
            db.add(registry_entry)
            db.commit()
        finally:
            db.close()

        _increment_sent_count(account_id)

        return WorkerResult(
            success=True,
            status="sent",
            platform_message_id=str(sent_message.id),
            retryable=False,
        )

    except FloodWaitError as exc:
        _mark_account_unhealthy(account_id, f"FloodWait: {exc.seconds}s")
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="telegram_flood_wait",
            error_message=f"»«Ìœ {exc.seconds} À«‰ÌÂ ’»— ò—œ.",
            retryable=True,
        )

    except PeerFloodError:
        _mark_account_unhealthy(account_id, "PeerFloodError")
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="telegram_peer_flood",
            error_message="«ò«‰  »Â „ÕœÊœÌ  ÃœÌ ŒÊ—œÂ.",
            retryable=False,
        )

    except UserPrivacyRestrictedError:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="telegram_privacy_restricted",
            error_message=" ‰ŸÌ„«  Õ—Ì„ Œ’Ê’Ì «Ì‰ ò«—»— «Ã«“Â ÅÌ«„ ‰„ÌùœÂœ.",
            retryable=False,
        )

    except Exception as exc:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="telegram_mtproto_error",
            error_message=str(exc)[:300],
            retryable=True,
        )

    finally:
        if client:
            await client.disconnect()
