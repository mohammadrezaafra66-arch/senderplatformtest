"""ÑÇåÇäÏÇÒí session ÊáÑÇã MTProto ÈÇ ÔãÇÑå ãæÈÇíá (OTP + ÑãÒ Ïæ ãÑÍáåÇí)."""

from __future__ import annotations

from pathlib import Path

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError

from core_engine.config import get_settings
from core_engine.models import SessionType
from sqlalchemy.orm import Session


_pending_clients: dict[int, TelegramClient] = {}


async def start_phone_login(account_id: int, phone_number: str) -> dict:
    """ãÑÍáå Çæá: ˜Ï ÊÇííÏ Èå ÔãÇÑå ãæÈÇíá ÇÑÓÇá ãíÔæÏ."""
    settings = get_settings()
    session_dir = Path("storage/telegram_mtproto_sessions")
    session_dir.mkdir(parents=True, exist_ok=True)
    session_path = session_dir / f"setup_{account_id}.session"

    client = TelegramClient(
        str(session_path),
        int(settings.TELEGRAM_API_ID),
        settings.TELEGRAM_API_HASH,
    )
    await client.connect()

    sent = await client.send_code_request(phone_number)
    _pending_clients[account_id] = client

    return {
        "status": "code_sent",
        "phone_code_hash": sent.phone_code_hash,
        "message": "˜Ï ÊÇííÏ Èå ÊáÑÇã ÔãÇ ÇÑÓÇá ÔÏ.",
    }


async def verify_phone_code(
    db: Session,
    account_id: int,
    phone_number: str,
    code: str,
    two_step_password: str | None = None,
) -> dict:
    """ãÑÍáå Ïæã: ˜Ï æÇÑÏ ãíÔæÏ — ÇÑ ÑãÒ Ïæ ãÑÍáåÇí İÚÇá ÈæÏ¡ Âä åã áÇÒã ÇÓÊ."""
    client = _pending_clients.get(account_id)
    if client is None:
        return {"status": "error", "message": "ÇÈÊÏÇ ÈÇíÏ ãÑÍáå ÇÑÓÇá ˜Ï Øí ÔæÏ."}

    try:
        await client.sign_in(phone=phone_number, code=code)
    except SessionPasswordNeededError:
        if not two_step_password:
            return {
                "status": "needs_2fa",
                "message": "Çíä Ç˜ÇäÊ ÑãÒ Ïæ ãÑÍáåÇí ÏÇÑÏ. áØİÇğ ÑãÒ ÑÇ åã æÇÑÏ ˜äíÏ.",
            }
        await client.sign_in(password=two_step_password)
    except PhoneCodeInvalidError:
        return {"status": "error", "message": "˜Ï æÇÑÏ ÔÏå ÇÔÊÈÇå ÇÓÊ."}

    session_path = Path(f"storage/telegram_mtproto_sessions/setup_{account_id}.session")
    session_bytes = session_path.read_bytes()

    from core_engine.services.session_storage import store_channel_session
    store_channel_session(
        db,
        account_id=account_id,
        session_type=SessionType.MTPROTO_SESSION,
        plaintext=session_bytes,
    )

    final_path = Path(f"storage/telegram_mtproto_sessions/account_{account_id}.session")
    session_path.rename(final_path)

    await client.disconnect()
    del _pending_clients[account_id]

    from core_engine.models import TelegramAccountPool
    from datetime import datetime
    settings = get_settings()
    pool_entry = TelegramAccountPool(
        account_id=account_id,
        warm_up_started_at=datetime.utcnow(),
        is_warmed_up=False,
        daily_cap_today=getattr(settings, "TELEGRAM_WARMUP_START_CAP", 10),
    )
    db.add(pool_entry)
    db.commit()

    return {"status": "session_saved", "account_id": account_id}
