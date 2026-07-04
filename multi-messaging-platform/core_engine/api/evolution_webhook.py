import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core_engine.database import SessionLocal
from core_engine.models import ChannelSession

logger = logging.getLogger("evolution_webhook")

router = APIRouter(prefix="/webhooks/whatsapp", tags=["evolution-webhook"])


def _extract_account_id(instance_name: str) -> int | None:
    """از instance_name مثل 'mmp-whatsapp-3' عدد account_id را استخراج می‌کند."""
    if not instance_name:
        return None
    m = re.search(r"(\d+)$", instance_name.strip())
    return int(m.group(1)) if m else None


def _normalize_state(state: str) -> str:
    """state خام Evolution را به evolution_status استاندارد نگاشت می‌کند."""
    s = (state or "").strip().lower()
    if s == "open":
        return "connected"
    if s in ("close", "closed"):
        return "disconnected"
    if s == "connecting":
        return "connecting"
    return s or "unknown"


@router.post("/evolution")
async def evolution_webhook_receiver(request: Request):
    """دریافت و پردازش رویدادهای webhook از Evolution API."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    event = (payload.get("event") or "").strip().lower()
    instance_name = payload.get("instance") or ""
    data = payload.get("data") or {}

    # فقط رویدادهای connection.update را پردازش می‌کنیم
    if event in ("connection.update", "connection_update"):
        state = data.get("state") or ""
        status_reason = data.get("statusReason")
        account_id = _extract_account_id(instance_name)
        normalized = _normalize_state(state)

        logger.info(
            "evolution_webhook_connection instance=%s account_id=%s state=%s reason=%s",
            instance_name, account_id, state, status_reason,
        )

        if account_id is not None:
            db = SessionLocal()
            try:
                cs = (
                    db.query(ChannelSession)
                    .filter(ChannelSession.account_id == account_id)
                    .first()
                )
                if cs is not None:
                    cs.evolution_status = normalized
                    now = datetime.now(timezone.utc)
                    cs.updated_at = now

                    if normalized == "connected":
                        cs.connected_at = now
                        cs.disconnected_at = None
                        cs.authorization_state = "authorized"
                        cs.socket_state = "online"
                        cs.reconnect_attempts = 0
                    elif normalized == "disconnected":
                        cs.disconnected_at = now
                        cs.socket_state = "offline"

                    # تشخیص logout دائمی (401) — نیاز به QR جدید، auto-reconnect بی‌فایده است
                    if status_reason == 401:
                        cs.authorization_state = "blocked"
                        cs.socket_state = "offline"
                        logger.warning(
                            "evolution_webhook_401_logout account_id=%s instance=%s "
                            "NEEDS_NEW_QR — auto-reconnect skipped",
                            account_id, instance_name,
                        )

                    db.commit()
                    logger.info(
                        "evolution_webhook_db_updated account_id=%s status=%s auth=%s socket=%s",
                        account_id,
                        normalized,
                        cs.authorization_state,
                        cs.socket_state,
                    )
            except Exception as exc:
                db.rollback()
                logger.error(
                    "evolution_webhook_db_error account_id=%s err=%s",
                    account_id, str(exc),
                )
            finally:
                db.close()

    return JSONResponse(content={"received": True})
