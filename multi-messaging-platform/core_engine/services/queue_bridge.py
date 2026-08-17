"""Bridge DB-staged messages into real worker Redis queues.

This module is intentionally separate from:
- core_engine.services.phase4_staging (read-only inspection)
- core_engine.services.queue_manager (dry-run/shadow queue manager)

This is the first place where we push to the real worker delivery queues.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import (
    Campaign,
    CampaignStatus,
    Message,
    PlatformType,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.services.consent_service import get_consent_block_reason
from core_engine.services.campaign_render import (
    RENDER_CONTENT_MISMATCH,
    RenderContentMismatchError,
    verify_render_content,
)
from core_engine.services.campaign_dispatch import (
    blocked_rubika_account_ids,
    claim_ready_items,
    count_in_flight,
    dispatch_settings,
    maybe_safety_pause_running_campaigns,
    payload_is_rubika,
    rotate_campaign_ids,
    running_campaign_ids_with_ready_items,
)
from core_engine.services.campaign_inflight import (
    acquire_send_lease,
    flush_due_delayed_retries,
    reserve_account_inflight,
)
from core_engine.services.campaign_preflight import (
    CAMPAIGN_CIRCUIT_OPEN,
    load_send_windows,
    log_campaign_safety_event,
)
from core_engine.services.redis_client import get_redis_client
from workers.redis_keys import campaign_dispatch_fairness_key, queue_key

logger = logging.getLogger(__name__)

def _is_baileys_mode() -> bool:
    return get_settings().WHATSAPP_DELIVERY_MODE.strip().lower() == "baileys"



async def push_staged_items_to_worker_queue(
    db: Session,
    batch_size: int | None = None,
) -> dict[str, int]:
    settings = get_settings()
    if not settings.REAL_QUEUE_PUSH_ENABLED:
        return {
            "pushed": 0,
            "skipped_consent": 0,
            "skipped_no_account": 0,
            "skipped_invalid": 0,
            "failed": 0,
            "deferred_backpressure": 0,
        }

    cfg = dispatch_settings(settings)
    effective_batch = int(batch_size) if batch_size is not None else int(cfg["batch_size"])
    pushed = 0
    skipped_consent = 0
    skipped_no_account = 0
    skipped_invalid = 0
    failed = 0
    deferred_backpressure = 0

    # (a) Claim items using row-level locking and SKIP LOCKED.
    # Items staged outside the normal prepare path have no rendered_message_id.
    # They carry no verifiable rendered text, so they are never claimed — their
    # status is left untouched rather than being silently sent or failed.
    incomplete_items = (
        db.query(StagedQueueItem)
        .join(Campaign, StagedQueueItem.campaign_id == Campaign.id)
        .filter(
            StagedQueueItem.status == StagedQueueItemStatus.READY.value,
            Campaign.status == CampaignStatus.RUNNING.value,
            StagedQueueItem.rendered_message_id.is_(None),
        )
        .count()
    )
    if incomplete_items:
        logger.warning(
            "queue bridge is ignoring %s staged item(s) with no rendered_message_id; "
            "re-prepare the campaign to rebuild them",
            incomplete_items,
        )

    redis = get_redis_client()
    try:
        from datetime import datetime, timezone

        await flush_due_delayed_retries(redis, now_unix=datetime.now(timezone.utc).timestamp())
    except Exception:
        pass

    campaign_ids = running_campaign_ids_with_ready_items(db)
    cursor = 0
    try:
        cursor = int(await redis.incr(campaign_dispatch_fairness_key()))
    except Exception:
        cursor = 0
    ordered_ids = rotate_campaign_ids(campaign_ids, cursor)

    in_flight = count_in_flight(db, ordered_ids)
    max_in_flight = int(cfg["max_in_flight"])
    if in_flight >= max_in_flight:
        log_campaign_safety_event(
            "campaign_dispatch_backpressure",
            campaign_id=ordered_ids[0] if ordered_ids else 0,
            code="MAX_IN_FLIGHT",
            extra={"in_flight": in_flight, "max": max_in_flight},
        )
        return {
            "pushed": 0,
            "skipped_consent": 0,
            "skipped_no_account": 0,
            "skipped_invalid": 0,
            "failed": 0,
            "deferred_backpressure": 0,
        }

    room = max(0, max_in_flight - in_flight)
    effective_batch = min(effective_batch, room)
    per_campaign = int(cfg["per_campaign"])
    if len(ordered_ids) <= 1:
        per_campaign = effective_batch
    else:
        per_campaign = max(
            1,
            min(per_campaign, max(1, effective_batch // len(ordered_ids))),
        )

    circuit_open = False
    circuit_unknown = False
    windows = []
    try:
        from core_engine.services.rubika_circuit import get_circuit_snapshot
        from workers.config import get_worker_settings

        await redis.ping()
        wsettings = get_worker_settings()
        snap = await get_circuit_snapshot(
            redis,
            probe_budget=int(wsettings.RUBIKA_CIRCUIT_PROBE_BUDGET),
            window_seconds=int(wsettings.RUBIKA_CIRCUIT_WINDOW_SECONDS),
        )
        circuit_open = snap.state == "open"
        windows = load_send_windows(db)
        if circuit_open:
            await maybe_safety_pause_running_campaigns(db, redis, circuit_open=True)
    except Exception:
        circuit_unknown = True

    blocked_accounts: set[int] = set()
    if circuit_unknown:
        # Fail closed for Rubika: do not claim Rubika rows when circuit/quota
        # state cannot be evaluated. Other platforms still dispatch.
        rubika_running = (
            db.query(Campaign.id)
            .filter(
                Campaign.id.in_(ordered_ids or [0]),
                Campaign.platform == PlatformType.RUBIKA,
            )
            .all()
        )
        ordered_ids = [
            cid for cid in ordered_ids if cid not in {int(row[0]) for row in rubika_running}
        ]
    else:
        try:
            blocked_accounts = await blocked_rubika_account_ids(
                db,
                redis,
                ordered_ids,
                circuit_open=circuit_open,
                windows=windows,
            )
        except Exception:
            blocked_accounts = set()
            if circuit_open:
                blocked_accounts = await blocked_rubika_account_ids(
                    db, redis, ordered_ids, circuit_open=True, windows=windows
                )

    claimed_items = claim_ready_items(
        db,
        campaign_ids=ordered_ids,
        batch_size=effective_batch,
        per_campaign=per_campaign,
        blocked_account_ids=blocked_accounts,
    )

    if not claimed_items:
        return {
            "pushed": 0,
            "skipped_consent": 0,
            "skipped_no_account": 0,
            "skipped_invalid": 0,
            "failed": 0,
            "deferred_backpressure": 0,
        }

    for item in claimed_items:
        item.status = StagedQueueItemStatus.PUSHING.value

    # Commit early to release locks quickly.
    db.commit()

    message_ids = {
        int(item.queue_payload["message_id"])
        for item in claimed_items
        if item.queue_payload
        and str(item.queue_payload.get("message_id", "")).isdigit()
    }
    messages_by_id = (
        {
            message.id: message
            for message in db.query(Message).filter(Message.id.in_(message_ids)).all()
        }
        if message_ids
        else {}
    )

    # (b) Process claimed items after releasing locks.
    for item in claimed_items:
        try:
            # Never fall back to placeholder or stale text: an item without a
            # usable rendered payload is parked as SKIPPED with an explicit
            # reason so it stays visible instead of going out wrong.
            raw_final = (item.queue_payload or {}).get("final_text")
            payload_text = "" if raw_final is None else str(raw_final)
            if not item.queue_payload or payload_text.strip() == "":
                item.status = StagedQueueItemStatus.SKIPPED.value
                item.skip_reason = "invalid_payload:missing_rendered_text"
                skipped_invalid += 1
                logger.warning(
                    "queue bridge skipped staged_item=%s: no rendered text in payload",
                    item.id,
                )
                db.commit()
                continue

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

            payload: dict[str, Any] = dict(item.queue_payload or {})
            try:
                message_id = int(payload["message_id"])
                payload_account_id = int(payload["account_id"])
            except (KeyError, TypeError, ValueError):
                item.status = StagedQueueItemStatus.SKIPPED.value
                item.skip_reason = "invalid_payload:missing_sender_assignment"
                skipped_invalid += 1
                db.commit()
                continue

            message = messages_by_id.get(message_id)
            if message is None:
                item.status = StagedQueueItemStatus.SKIPPED.value
                item.skip_reason = "invalid_payload:message_not_found"
                skipped_invalid += 1
                db.commit()
                continue
            if (
                message.campaign_id != item.campaign_id
                or message.contact_id != item.contact_id
                or message.account_id != payload_account_id
            ):
                item.status = StagedQueueItemStatus.SKIPPED.value
                item.skip_reason = "invalid_payload:sender_assignment_mismatch"
                skipped_invalid += 1
                db.commit()
                continue

            alt_text = payload.get("message_text")
            if alt_text is not None and str(alt_text) != payload_text:
                item.status = StagedQueueItemStatus.SKIPPED.value
                item.skip_reason = RENDER_CONTENT_MISMATCH
                skipped_invalid += 1
                logger.error(
                    "queue bridge skipped staged_item=%s campaign_id=%s: conflicting text fields",
                    item.id,
                    item.campaign_id,
                )
                db.commit()
                continue

            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
            try:
                verify_render_content(
                    final_text=payload_text,
                    metadata=metadata,
                    message_rendered_text=message.rendered_text,
                )
            except RenderContentMismatchError:
                item.status = StagedQueueItemStatus.SKIPPED.value
                item.skip_reason = RENDER_CONTENT_MISMATCH
                skipped_invalid += 1
                logger.error(
                    "queue bridge skipped staged_item=%s campaign_id=%s: %s",
                    item.id,
                    item.campaign_id,
                    RENDER_CONTENT_MISMATCH,
                )
                db.commit()
                continue

            if payload_is_rubika(item) and (circuit_open or circuit_unknown):
                item.status = StagedQueueItemStatus.READY.value
                item.skip_reason = (
                    CAMPAIGN_CIRCUIT_OPEN if circuit_open else "CAMPAIGN_CAPACITY_UNKNOWN"
                )
                deferred_backpressure += 1
                db.commit()
                continue

            if payload_is_rubika(item):
                lease_ok = True
                inflight_ok = True
                try:
                    lease_ok = await acquire_send_lease(
                        redis,
                        message_id,
                        token=f"bridge:{item.id}",
                        ttl_seconds=int(cfg["inflight_ttl"]),
                    )
                    if lease_ok:
                        inflight_ok = await reserve_account_inflight(
                            redis,
                            payload_account_id,
                            message_id,
                            max_in_flight=int(cfg["per_account"]),
                            ttl_seconds=int(cfg["inflight_ttl"]),
                        )
                except Exception:
                    lease_ok = False
                    inflight_ok = False
                if not lease_ok or not inflight_ok:
                    item.status = StagedQueueItemStatus.READY.value
                    item.skip_reason = None
                    deferred_backpressure += 1
                    log_campaign_safety_event(
                        "campaign_dispatch_backpressure",
                        campaign_id=int(item.campaign_id),
                        code="ACCOUNT_IN_FLIGHT" if lease_ok else "DUPLICATE_LEASE",
                        extra={"account_id": payload_account_id, "message_id": message_id},
                    )
                    db.commit()
                    continue

            if platform_enum == PlatformType.WHATSAPP and _is_baileys_mode():
                from core_engine.services.baileys_queue import enqueue_baileys_from_worker_payload

                await enqueue_baileys_from_worker_payload(db, payload, route="campaign")
            else:
                key = queue_key(platform_enum.value, message.account_id)
                raw_payload = json.dumps(payload, ensure_ascii=False)
                await redis.rpush(key, raw_payload)

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
        "skipped_invalid": skipped_invalid,
        "failed": failed,
        "deferred_backpressure": deferred_backpressure,
    }

