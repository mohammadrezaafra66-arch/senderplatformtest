"""Bridge DB-staged messages into real worker Redis queues.

This module is intentionally separate from:
- core_engine.services.phase4_staging (read-only inspection)
- core_engine.services.queue_manager (dry-run/shadow queue manager)

This is the first place where we push to the real worker delivery queues.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    AccountStatus,
    PlatformType,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.services.consent_service import get_consent_block_reason
from core_engine.services.redis_client import get_redis_client
from workers.redis_keys import queue_key

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async Redis call from sync code.

    This function is used to keep the public API synchronous as requested.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # If we're already in an event loop, we can't blockingly wait here.
    # For now, schedule and let it run; callers should keep bridge synchronous.
    return asyncio.create_task(coro)


def push_staged_items_to_worker_queue(
    db: Session,
    batch_size: int = 100,
) -> dict[str, int]:
    pushed = 0
    skipped_consent = 0
    skipped_no_account = 0
    failed = 0

    rr_cursor: dict[str, int] = {}

    # (a) Claim items using row-level locking and SKIP LOCKED.
    claimed_items = (
        db.query(StagedQueueItem)
        .filter(StagedQueueItem.status == StagedQueueItemStatus.READY.value)
        .order_by(StagedQueueItem.id.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
        .all()
    )

    if not claimed_items:
        return {
            "pushed": 0,
            "skipped_consent": 0,
            "skipped_no_account": 0,
            "failed": 0,
        }

    for item in claimed_items:
        item.status = StagedQueueItemStatus.PUSHING.value

    # Commit early to release locks quickly.
    db.commit()

    redis = get_redis_client()

    # (b) Process claimed items after releasing locks.
    for item in claimed_items:
        try:
            block_reason = get_consent_block_reason(
                db,
                contact_id=item.contact_id,
                platform=item.channel,
            )
            if block_reason is not None:
                item.status = StagedQueueItemStatus.SKIPPED.value
                item.skip_reason = f"consent_blocked:{block_reason}"
                skipped_consent += 1
                db.commit()
                continue

            try:
                platform_enum = PlatformType(str(item.channel).strip().lower())
            except Exception:
                platform_enum = None

            if platform_enum is None:
                item.status = StagedQueueItemStatus.SKIPPED.value
                item.skip_reason = f"invalid_platform:{item.channel}"
                failed += 1
                db.commit()
                continue

            accounts = (
                db.query(Account)
                .filter(
                    Account.platform == platform_enum,
                    Account.status == AccountStatus.ACTIVE,
                )
                .order_by(Account.id.asc())
                .all()
            )

            if not accounts:
                item.status = StagedQueueItemStatus.READY.value
                skipped_no_account += 1
                db.commit()
                continue

            platform_key = platform_enum.value
            idx = rr_cursor.get(platform_key, 0) % len(accounts)
            account = accounts[idx]
            rr_cursor[platform_key] = idx + 1

            payload: dict[str, Any] = dict(item.queue_payload or {})
            payload["account_id"] = int(account.id)
            item.queue_payload = payload  # re-assign for JSONB change detection

            key = queue_key(item.channel, account.id)
            raw_payload = json.dumps(payload, ensure_ascii=False)
            _run_async(redis.rpush(key, raw_payload))

            item.status = StagedQueueItemStatus.QUEUED.value
            item.skip_reason = None
            pushed += 1
            db.commit()
        except Exception as exc:
            logger.exception("queue bridge failed for staged_item=%s", getattr(item, "id", None))
            item.status = StagedQueueItemStatus.READY.value
            item.skip_reason = f"bridge_failed:{exc.__class__.__name__}"
            failed += 1
            db.commit()

    return {
        "pushed": pushed,
        "skipped_consent": skipped_consent,
        "skipped_no_account": skipped_no_account,
        "failed": failed,
    }

