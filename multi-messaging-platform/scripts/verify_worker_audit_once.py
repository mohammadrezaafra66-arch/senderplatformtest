"""One-shot UI-path send: enqueue ops test payload, run worker once, print audit rows."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from sqlalchemy import text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

os.environ.setdefault("DATABASE_URL", "postgresql://mmp_user:mmp_pass@127.0.0.1:5433/mmp_db")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("WHATSAPP_DELIVERY_MODE", "web")
os.environ.setdefault("WHATSAPP_ACCOUNT_IDS", "248")
local_profile = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "SenderPlatform",
    "mmp-whatsapp",
)
os.environ.setdefault("WHATSAPP_WEB_PROFILE_ROOT", local_profile)
os.environ.setdefault("WHATSAPP_WEB_HEADLESS", "false")
os.environ.setdefault("DRY_RUN", "false")
os.environ.setdefault("REAL_MESSAGE_SENDING_ENABLED", "true")
os.environ.setdefault("CHANNEL_CONNECTORS_ENABLED", "true")
os.environ.setdefault("WORKER_EXECUTION_ENABLED", "true")

ACCOUNT_ID = 248
RECIPIENT = sys.argv[1] if len(sys.argv) > 1 else "989122270261"
MESSAGE_TEXT = (
    sys.argv[2]
    if len(sys.argv) > 2
    else "تست audit کامل — مسیر UI/worker (Jun 20)"
)


async def main() -> int:
    from core_engine.database import SessionLocal
    from core_engine.models import Account
    from core_engine.services.operational_send import build_test_worker_payload
    from core_engine.services.redis_client import get_redis_client
    from workers.config import WorkerSettings
    from workers.redis_keys import queue_key
    from workers.whatsapp_pool_worker import WhatsAppPoolWorker

    session = SessionLocal()
    try:
        account = session.query(Account).filter(Account.id == ACCOUNT_ID).first()
        if account is None:
            print(f"Account {ACCOUNT_ID} not found.")
            return 1
        before = session.execute(
            text("SELECT COALESCE(MAX(id), 0) FROM audit_logs")
        ).scalar()
    finally:
        session.close()

    payload = build_test_worker_payload(
        account,
        message_text=MESSAGE_TEXT,
        recipient=RECIPIENT,
    )
    redis = get_redis_client()
    key = queue_key("whatsapp", ACCOUNT_ID)
    await redis.rpush(key, json.dumps(payload.model_dump(), ensure_ascii=False))
    print(f"Enqueued ops-test payload message_id={payload.message_id}")

    worker = WhatsAppPoolWorker(
        account_ids=[ACCOUNT_ID],
        redis_url=os.environ["REDIS_URL"],
        database_url=os.environ["DATABASE_URL"],
        settings=WorkerSettings(
            DRY_RUN=False,
            REAL_MESSAGE_SENDING_ENABLED=True,
            CHANNEL_CONNECTORS_ENABLED=True,
            WHATSAPP_DELIVERY_MODE="web",
            WHATSAPP_WEB_HEADLESS=os.environ.get("WHATSAPP_WEB_HEADLESS", "false").lower()
            == "true",
            WHATSAPP_DISTRIBUTED_LOCK_ENABLED=False,
            WHATSAPP_MIN_SEND_DELAY_SECONDS=0,
        ),
    )
    await worker.connect()
    try:
        print("Running worker once (Playwright send — may take up to 90s)...")
        await worker.run_once()
    finally:
        await worker.disconnect()

    session = SessionLocal()
    try:
        rows = session.execute(
            text(
                """
                SELECT id, timestamp, username, action, details
                FROM audit_logs
                WHERE id > :before
                ORDER BY id
                """
            ),
            {"before": before},
        ).fetchall()
    finally:
        session.close()

    if not rows:
        print("No new audit rows — send may have failed or worker found empty queue.")
        return 2

    print("\n=== New audit rows ===")
    for row in rows:
        print(
            f"id={row.id} ts={row.timestamp} user={row.username} "
            f"action={row.action} details={row.details}"
        )
    print(f"\nDone at {datetime.now(timezone.utc).isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
