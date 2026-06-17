"""Enqueue یک payload تستی به صف Worker برای تست زیرساخت.

Worker infrastructure dev tool only — NOT for Phase 4 campaign flow.
Phase 4 must use database staging only. Do not call from core_engine APIs.
"""

from __future__ import annotations

import asyncio
import json
import sys

from redis.asyncio import Redis

from workers.config import get_worker_settings
from workers.logging_utils import get_worker_logger, log_worker_event
from workers.redis_keys import queue_key

TEST_PAYLOAD = {
    "message_id": "test-msg-1",
    "campaign_id": "test-campaign-1",
    "contact_id": "test-contact-1",
    "account_id": "1",
    "platform": "bale",
    "recipient": "+989120000000",
    "recipient_type": "phone_number",
    "message_text": "Test message from worker infrastructure",
    "media_url": None,
    "dedupe_key": "test-dedupe-1",
    "attempt": 1,
    "metadata": {"source": "dev_enqueue_test_payload"},
}


async def main() -> int:
    settings = get_worker_settings()
    platform = settings.WORKER_PLATFORM.lower().strip()
    account_id = settings.WORKER_ACCOUNT_ID

    if platform != "bale":
        TEST_PAYLOAD["platform"] = platform
        TEST_PAYLOAD["account_id"] = str(account_id)

    key = queue_key(platform, account_id)
    logger = get_worker_logger(
        "dev_enqueue_test_payload",
        platform=platform,
        account_id=account_id,
    )

    redis_client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await redis_client.ping()
        payload_json = json.dumps(TEST_PAYLOAD, ensure_ascii=False)
        await redis_client.rpush(key, payload_json)
        log_worker_event(
            logger,
            event="test_payload_enqueued",
            status="ok",
            platform=platform,
            account_id=account_id,
        )
        print(
            json.dumps(
                {
                    "success": True,
                    "event": "test_payload_enqueued",
                    "queue_key": key,
                    "message_id": TEST_PAYLOAD["message_id"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
