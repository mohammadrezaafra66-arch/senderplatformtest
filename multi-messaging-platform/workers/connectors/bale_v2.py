"""Bale Bot API connector v2 — with python-bale-bot library."""

from __future__ import annotations
import bale
from workers.payloads import WorkerPayload, WorkerResult
from workers.config import WorkerSettings
from workers.db import get_db_session
from core_engine.models import SessionType


def _load_token(account_id: int) -> str:
    from workers.session_access import load_account_session_plaintext
    from workers.connectors.bale import parse_bale_bot_token
    db = get_db_session()
    try:
        plaintext = load_account_session_plaintext(db, account_id=account_id,
                                                    session_type=SessionType.API_TOKEN)
        return parse_bale_bot_token(plaintext)
    finally:
        db.close()


async def deliver_bale_v2_live(payload: WorkerPayload, settings: WorkerSettings) -> WorkerResult:
    token = _load_token(int(payload.account_id))
    chat_id = str(payload.recipient).strip()

    bot = bale.Bot(token=token)
    try:
        await bot.connect()

        if payload.media_type == "photo" and payload.media_url:
            msg = await bot.send_photo(chat_id,
                bale.InputFile(await download_bytes(payload.media_url)),
                caption=payload.message_text
            )
        elif payload.media_type == "document" and payload.media_url:
            msg = await bot.send_document(chat_id,
                bale.InputFile(await download_bytes(payload.media_url)),
                caption=payload.message_text
            )
        else:
            msg = await bot.send_message(chat_id, payload.message_text)

        return WorkerResult(
            success=True, status="delivered",
            platform_message_id=f"bale-{msg.message_id}",
            retryable=False,
        )
    except bale.RateLimited:
        return WorkerResult(success=False, status="failed_retryable",
                            error_code="bale_rate_limited", retryable=True)
    except bale.NotFound:
        return WorkerResult(success=False, status="failed_permanent",
                            error_code="bale_chat_not_found", retryable=False)
    except bale.Forbidden:
        return WorkerResult(success=False, status="failed_permanent",
                            error_code="bale_forbidden", retryable=False)
    except Exception as e:
        return WorkerResult(success=False, status="failed_retryable",
                            error_code="bale_error", error_message=str(e)[:300], retryable=True)
    finally:
        await bot.close()
