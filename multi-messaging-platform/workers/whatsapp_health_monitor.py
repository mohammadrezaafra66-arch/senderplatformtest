import asyncio
import logging
import os
import re
from datetime import datetime, timezone

import httpx

from core_engine.models import ChannelSession
from workers.db import get_db_session

logger = logging.getLogger("whatsapp_health_monitor")

INSTANCE_PREFIX = "mmp-whatsapp-"


def _extract_account_id(instance_name: str) -> int | None:
    if not instance_name or not instance_name.startswith(INSTANCE_PREFIX):
        return None
    m = re.search(r"(\d+)$", instance_name.strip())
    return int(m.group(1)) if m else None


def _normalize_status(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s == "open":
        return "connected"
    if s in ("close", "closed"):
        return "disconnected"
    if s == "connecting":
        return "connecting"
    return s or "unknown"


async def _fetch_instances(base_url: str, api_key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{base_url}/instance/fetchInstances",
            headers={"apikey": api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []


def _sync_one(account_id: int, new_status: str, owner_jid: str | None) -> bool:
    """DB را برای یک اکانت به‌روز می‌کند. True اگر تغییری رخ داد."""
    session = get_db_session()
    try:
        cs = (
            session.query(ChannelSession)
            .filter(ChannelSession.account_id == account_id)
            .first()
        )
        if cs is None:
            return False
        old_status = (cs.evolution_status or "").strip().lower()
        if old_status == new_status:
            return False

        cs.evolution_status = new_status
        now = datetime.now(timezone.utc)
        cs.updated_at = now
        if new_status == "connected":
            cs.connected_at = now
            cs.disconnected_at = None
        elif new_status == "disconnected":
            cs.disconnected_at = now
        session.commit()
        logger.info(
            "health_monitor_status_change account_id=%s %s -> %s",
            account_id, old_status, new_status,
        )
        return True
    except Exception as exc:
        session.rollback()
        logger.error("health_monitor_db_error account_id=%s err=%s", account_id, str(exc))
        return False
    finally:
        session.close()


async def _monitor_loop() -> None:
    base_url = os.environ.get("EVOLUTION_API_BASE_URL", "http://mmp_evolution_api:8080").rstrip("/")
    api_key = os.environ.get("EVOLUTION_API_KEY", "")
    interval = int(os.environ.get("HEALTH_MONITOR_INTERVAL_SECONDS", "30"))

    logger.info("health_monitor_started base_url=%s interval=%ss", base_url, interval)

    while True:
        try:
            instances = await _fetch_instances(base_url, api_key)
            for inst in instances:
                account_id = _extract_account_id(inst.get("name", ""))
                if account_id is None:
                    continue
                new_status = _normalize_status(inst.get("connectionStatus", ""))
                owner_jid = inst.get("ownerJid")
                _sync_one(account_id, new_status, owner_jid)
        except httpx.HTTPError as exc:
            logger.warning("health_monitor_fetch_error err=%s", str(exc))
        except Exception as exc:
            logger.error("health_monitor_loop_error err=%s", str(exc))
        await asyncio.sleep(interval)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_monitor_loop())


if __name__ == "__main__":
    main()
