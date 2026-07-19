"""ورود به بله با شماره موبایل — OTP."""

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError

_pending_clients: dict[int, TelegramClient] = {}


async def start_bale_phone_login(account_id: int, phone_number: str) -> dict:
    settings = get_settings()
    client = TelegramClient(
        f"storage/bale_sessions/setup_{account_id}",
        int(settings.BALE_API_ID),
        settings.BALE_API_HASH,
        # NOTE: DC address for Bale MTProto servers must be configured here once discovered
    )
    await client.connect()
    sent = await client.send_code_request(phone_number)
    _pending_clients[account_id] = client
    return {"status": "code_sent", "phone_code_hash": sent.phone_code_hash}


async def verify_bale_phone_code(db, account_id, phone_number, code, password=None) -> dict:
    # Similar to telegram_session_setup.verify_phone_code
    # To be implemented once MTProto server addresses for Bale are confirmed
    ...
