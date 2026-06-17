"""Phase 4 campaign message preparation — mock render and DB staging only."""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import (
    Campaign,
    CampaignStatus,
    Contact,
    ProductSnapshot,
    RenderedMessage,
    StagedQueueItem,
)
from core_engine.schemas.phase4 import PrepareMessagesRequest, PrepareMessagesResultResponse
from core_engine.services.gpt_orchestrator import build_product_context
from core_engine.services.phase4_utils import (
    build_full_name,
    build_staged_queue_payload,
    is_consent_allowed,
    normalize_consent_status,
)
from core_engine.services.product_snapshot import get_latest_valid_product_snapshot


def build_phase4_mock_final_text(contact: Contact, campaign: Campaign) -> str:
    display_name = (
        contact.full_name
        or build_full_name(contact.first_name, contact.last_name)
        or "مشتری"
    )
    goal_hint = (campaign.message_goal or "چند محصول موجود با قیمت روز").strip()
    return (
        f"{display_name} عزیز، {goal_hint} آماده بررسی است. "
        "این پیام فقط تست dry-run است و ارسال واقعی انجام نشده."
    )


def _parse_snapshot_expires_at(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _compute_effective_limit(
    *,
    allowed_contacts: int,
    request_limit: int | None,
    daily_limit: int | None,
    max_contacts: int | None,
) -> int:
    limits = [allowed_contacts]
    if request_limit is not None:
        limits.append(request_limit)
    if daily_limit is not None:
        limits.append(daily_limit)
    if max_contacts is not None:
        limits.append(max_contacts)
    return min(limits)


def prepare_campaign_messages(
    db: Session,
    campaign_id: int,
    request: PrepareMessagesRequest,
) -> PrepareMessagesResultResponse:
    if not request.force_mock_output:
        raise HTTPException(
            status_code=400,
            detail="Real GPT rendering is disabled in Phase 4 Step 5",
        )

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    snapshot_meta = get_latest_valid_product_snapshot(db)
    if not snapshot_meta.get("found"):
        raise HTTPException(
            status_code=400,
            detail=snapshot_meta.get("reason") or "No valid product snapshot found",
        )

    snapshot_id = int(snapshot_meta["snapshot_id"])
    snapshot = (
        db.query(ProductSnapshot)
        .filter(ProductSnapshot.id == snapshot_id)
        .first()
    )
    if snapshot is None:
        raise HTTPException(status_code=400, detail="No valid product snapshot found")

    product_context = build_product_context(db, include_products=True, max_products=3)
    used_products = bool(product_context.get("enabled"))
    snapshot_expires_at = _parse_snapshot_expires_at(
        product_context.get("snapshot_expires_at") or snapshot.expires_at
    )

    contacts = (
        db.query(Contact)
        .filter(Contact.campaign_id == campaign_id)
        .order_by(Contact.id.asc())
        .all()
    )

    total_contacts = len(contacts)
    allowed_contacts_list = [c for c in contacts if is_consent_allowed(c.consent_status)]
    allowed_contacts = len(allowed_contacts_list)
    blocked_count = sum(
        1
        for contact in contacts
        if normalize_consent_status(contact.consent_status) == "blocked"
    )
    skipped_contacts = total_contacts - allowed_contacts

    existing_staged: dict[int, StagedQueueItem] = {
        item.contact_id: item
        for item in db.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign_id)
        .all()
    }

    effective_limit = _compute_effective_limit(
        allowed_contacts=allowed_contacts,
        request_limit=request.limit,
        daily_limit=campaign.daily_limit,
        max_contacts=campaign.max_contacts,
    )
    limit_was_applied = effective_limit < allowed_contacts

    existing_ready_count = sum(
        1 for item in existing_staged.values() if item.status == "ready"
    )
    new_ready_slots = max(0, effective_limit - existing_ready_count)

    returned_items: list[StagedQueueItem] = []
    already_staged_count = 0
    newly_staged_count = 0

    for contact in allowed_contacts_list:
        existing_item = existing_staged.get(contact.id)
        if existing_item is not None:
            if existing_item.status == "ready":
                already_staged_count += 1
                returned_items.append(existing_item)
            continue

        if newly_staged_count >= new_ready_slots:
            continue

        final_text = build_phase4_mock_final_text(contact, campaign)

        rendered_message = RenderedMessage(
            campaign_id=campaign_id,
            contact_id=contact.id,
            channel=campaign.channel,
            final_text=final_text,
            render_mode="mock",
            used_kb=False,
            used_products=used_products,
            product_snapshot_id=snapshot_id,
            snapshot_expires_at=snapshot_expires_at,
            ready_for_queue=True,
            warnings=None,
        )
        db.add(rendered_message)
        db.flush()

        queue_payload = build_staged_queue_payload(
            campaign_id=campaign_id,
            contact_id=contact.id,
            rendered_message_id=rendered_message.id,
            channel=campaign.channel,
            phone=contact.phone,
            channel_handle=contact.channel_handle,
            final_text=final_text,
        )
        rendered_message.queue_payload = queue_payload

        staged_item = StagedQueueItem(
            campaign_id=campaign_id,
            contact_id=contact.id,
            rendered_message_id=rendered_message.id,
            channel=campaign.channel,
            status="ready",
            final_text=final_text,
            queue_payload=queue_payload,
            skip_reason=None,
        )
        db.add(staged_item)

        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Duplicate staged item detected for this campaign contact.",
            ) from exc

        existing_staged[contact.id] = staged_item
        returned_items.append(staged_item)
        newly_staged_count += 1

    ready_count = sum(1 for item in returned_items if item.status == "ready")
    if ready_count > 0 or existing_ready_count > 0:
        campaign.status = CampaignStatus.PREPARED.value

    try:
        db.commit()
        for item in returned_items:
            db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Failed to stage messages due to duplicate records.",
        ) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Failed to prepare campaign messages.",
        ) from exc

    settings = get_settings()
    from core_engine.schemas.phase4 import StagedQueueItemResponse

    return PrepareMessagesResultResponse(
        campaign_id=campaign_id,
        total_contacts=total_contacts,
        allowed_contacts=allowed_contacts,
        skipped_contacts=skipped_contacts,
        staged_count=len(returned_items),
        ready_count=ready_count,
        blocked_count=blocked_count,
        already_staged_count=already_staged_count,
        limit_applied=effective_limit if limit_was_applied else None,
        product_snapshot_id=snapshot_id,
        product_snapshot_valid=True,
        force_mock_output=request.force_mock_output,
        real_gpt_called=False,
        real_queue_push_enabled=settings.REAL_QUEUE_PUSH_ENABLED,
        redis_queue_pushed=False,
        items=[StagedQueueItemResponse.model_validate(item) for item in returned_items],
    )
