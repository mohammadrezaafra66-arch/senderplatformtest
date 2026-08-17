"""Phase 4 campaign message preparation — mock render and DB staging only."""

from __future__ import annotations

import random
import uuid

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import (
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    Message,
    RenderedMessage,
    StagedQueueItem,
)
from core_engine.schemas.phase4 import PrepareMessagesRequest, PrepareMessagesResultResponse
from core_engine.services.campaign_sender_assignment import resolve_campaign_sender_accounts
from core_engine.services.phase4_utils import (
    build_full_name,
    build_staged_queue_payload,
    is_consent_allowed,
    normalize_consent_status,
)
from core_engine.services.message_variation.placeholders import (
    TEMPLATE_PLACEHOLDER,
    template_fingerprint,
)
from core_engine.services.message_variation.errors import GptVariationError
from core_engine.services.message_variation.service import (
    assign_and_log,
    assignment_metadata,
    generate_validated_pool,
    pool_from_queue_payload,
)
from core_engine.services.product_feed.dto import MessageComposition
from core_engine.services.product_feed.errors import ProductFeedError
from core_engine.services.product_feed.service import (
    fetch_current_advertising_products,
    select_and_compose,
)


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


def build_campaign_template_variables(contact: Contact) -> dict[str, str]:
    """متغیرهای مجاز قالب برای یک مخاطب."""
    full_name = (
        contact.full_name
        or build_full_name(contact.first_name, contact.last_name)
        or ""
    )
    return {
        "first_name": (contact.first_name or "").strip(),
        "last_name": (contact.last_name or "").strip(),
        "full_name": full_name.strip(),
    }


def substitute_template_variables(template: str, contact: Contact) -> str:
    """جایگزینی متغیرهای مخاطب روی یک قالب از قبل تنوع‌داده‌شده یا خام."""
    variables = build_campaign_template_variables(contact)

    def _replace(match) -> str:
        name = match.group(1)
        return variables.get(name, match.group(0))

    return TEMPLATE_PLACEHOLDER.sub(_replace, template).strip()


def render_campaign_template(contact: Contact, campaign: Campaign) -> str:
    """متن واقعی کمپین را با جایگزینی متغیرهای مخاطب رندر می‌کند.

    متغیر ناشناخته دست‌نخورده باقی می‌ماند تا خطای تایپی کاربر بی‌صدا حذف نشود.
    """
    template = (campaign.template_text or "").strip()
    if not template:
        raise HTTPException(
            status_code=400,
            detail=(
                "Campaign has no template_text; set the message text before "
                "preparing, or call prepare with force_mock_output=true."
            ),
        )
    return substitute_template_variables(template, contact)


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


def _gpt_http_error(exc: GptVariationError) -> HTTPException:
    return HTTPException(status_code=400, detail=exc.http_detail())


def _product_http_error(exc: ProductFeedError) -> HTTPException:
    return HTTPException(status_code=400, detail=exc.http_detail())


def _product_metadata(composition: MessageComposition | None) -> dict | None:
    if composition is None or composition.snapshot is None:
        return None
    return {
        "frozen_product_snapshot": composition.snapshot.to_dict(),
        "prose_text": composition.prose_text,
        "immutable_product_block": composition.immutable_product_block,
        "product_heading": composition.heading,
    }


def _render_metadata(
    composition: MessageComposition | None,
    gpt_meta: dict | None,
) -> dict | None:
    meta: dict = {}
    if gpt_meta:
        meta.update(gpt_meta)
    product = _product_metadata(composition)
    if product:
        meta.update(product)
    return meta or None


def prepare_campaign_messages(
    db: Session,
    campaign_id: int,
    request: PrepareMessagesRequest,
) -> PrepareMessagesResultResponse:
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    product_feed = None
    used_products = False
    snapshot_id = None
    snapshot_expires_at = None
    prepare_nonce = uuid.uuid4().hex
    gpt_pool = None
    gpt_called = False

    # Contacts are attached to a campaign through campaign_recipients (see the
    # POST /campaigns handler); Contact.campaign_id is left NULL by the importer.
    recipient_rows = (
        db.query(CampaignRecipient, Contact)
        .join(Contact, CampaignRecipient.contact_id == Contact.id)
        .filter(CampaignRecipient.campaign_id == campaign_id)
        .order_by(CampaignRecipient.id.asc())
        .all()
    )
    contacts = [contact for _recipient, contact in recipient_rows]

    total_contacts = len(contacts)
    allowed_contacts_list = [c for c in contacts if is_consent_allowed(c.consent_status)]
    allowed_contact_ids = {contact.id for contact in allowed_contacts_list}
    allowed_recipient_rows = [
        (recipient, contact)
        for recipient, contact in recipient_rows
        if contact.id in allowed_contact_ids
    ]
    allowed_contacts = len(allowed_contacts_list)
    blocked_count = sum(
        1
        for contact in contacts
        if normalize_consent_status(contact.consent_status) == "blocked"
    )
    skipped_contacts = total_contacts - allowed_contacts

    sender_accounts = (
        resolve_campaign_sender_accounts(db, campaign)
        if allowed_recipient_rows
        else []
    )

    existing_staged: dict[int, StagedQueueItem] = {
        item.contact_id: item
        for item in db.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == campaign_id)
        .all()
    }
    final_message_ids = {
        recipient.final_message_id
        for recipient, _contact in allowed_recipient_rows
        if recipient.final_message_id is not None
    }
    existing_messages = (
        {
            message.id: message
            for message in db.query(Message)
            .filter(Message.id.in_(final_message_ids))
            .all()
        }
        if final_message_ids
        else {}
    )

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
    unstaged_allowed = sum(
        1 for _recipient, contact in allowed_recipient_rows if contact.id not in existing_staged
    )
    will_render = (existing_ready_count > 0) or (
        new_ready_slots > 0 and unstaged_allowed > 0
    )

    if (
        campaign.use_gpt
        and not request.force_mock_output
        and will_render
    ):
        fingerprint = template_fingerprint(campaign.template_text or "")
        for item in existing_staged.values():
            existing_pool = pool_from_queue_payload(item.queue_payload)
            if (
                existing_pool is not None
                and existing_pool.template_fingerprint == fingerprint
            ):
                gpt_pool = existing_pool
                break
        if gpt_pool is None:
            try:
                gpt_pool = generate_validated_pool(campaign.template_text or "")
            except GptVariationError as exc:
                raise _gpt_http_error(exc) from exc
            gpt_called = True

    if campaign.include_products and will_render:
        try:
            product_feed = fetch_current_advertising_products()
        except ProductFeedError as exc:
            raise _product_http_error(exc) from exc
        used_products = True

    returned_items: list[StagedQueueItem] = []
    already_staged_count = 0
    newly_staged_count = 0

    if request.force_mock_output:
        render_mode = "mock"
    elif campaign.use_gpt:
        render_mode = "gpt"
    else:
        render_mode = "template"

    def _render(contact: Contact) -> tuple[str, MessageComposition | None, dict | None]:
        gpt_meta = None
        if request.force_mock_output:
            prose = build_phase4_mock_final_text(contact, campaign)
        elif campaign.use_gpt:
            if gpt_pool is None:
                raise _gpt_http_error(GptVariationError("GPT_UNAVAILABLE"))
            assigned = assign_and_log(
                gpt_pool, campaign_id=campaign.id, contact_id=contact.id
            )
            prose = substitute_template_variables(assigned.text, contact)
            gpt_meta = assignment_metadata(gpt_pool, assigned)
        else:
            prose = render_campaign_template(contact, campaign)
        if not campaign.include_products:
            return prose, None, gpt_meta
        if product_feed is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "PRODUCT_FEED_UNAVAILABLE",
                    "message": "اطلاعات محصولات و قیمت‌های لحظه‌ای در دسترس نیست؛ آماده‌سازی کمپین شامل محصولات متوقف شد.",
                },
            )
        rng = random.Random(f"{campaign.id}:{contact.id}:{prepare_nonce}")
        try:
            composition = select_and_compose(prose, product_feed, rng=rng)
        except ProductFeedError as exc:
            raise _product_http_error(exc) from exc
        return composition.final_text, composition, gpt_meta

    for recipient_index, (recipient, contact) in enumerate(allowed_recipient_rows):
        assigned_account = sender_accounts[recipient_index % len(sender_accounts)]
        existing_item = existing_staged.get(contact.id)
        if existing_item is not None:
            if existing_item.status == "ready":
                # A ready item was never handed to a worker, so refresh it when
                # the campaign text has changed since it was staged. Items that
                # already moved on (queued/sent) are never touched.
                current_text, composition, gpt_meta = _render(contact)
                product_meta = _render_metadata(composition, gpt_meta)
                message = (
                    existing_messages.get(recipient.final_message_id)
                    if recipient.final_message_id is not None
                    else None
                )
                if message is not None and message.account_id != assigned_account.id:
                    db.rollback()
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "prepared_sender_assignment_mismatch",
                            "message": (
                                f"Message {message.id} has account {message.account_id}, "
                                f"but deterministic assignment requires {assigned_account.id}."
                            ),
                        },
                    )
                if message is None:
                    message = Message(
                        campaign_id=campaign_id,
                        account_id=assigned_account.id,
                        contact_id=contact.id,
                        rendered_text=current_text,
                        dedupe_key=f"campaign:{campaign_id}:contact:{contact.id}",
                    )
                    db.add(message)
                    db.flush()
                    recipient.final_message_id = message.id
                    existing_messages[message.id] = message
                else:
                    message.rendered_text = current_text

                if existing_item.final_text != current_text:
                    existing_item.final_text = current_text
                existing_item.queue_payload = build_staged_queue_payload(
                    campaign_id=campaign_id,
                    contact_id=contact.id,
                    rendered_message_id=existing_item.rendered_message_id,
                    message_id=message.id,
                    account_id=message.account_id,
                    channel=campaign.channel,
                    phone=contact.phone,
                    channel_handle=contact.channel_handle,
                    final_text=current_text,
                    metadata=product_meta,
                )
                if existing_item.rendered_message_id is not None:
                    stale_rendered = db.get(
                        RenderedMessage, existing_item.rendered_message_id
                    )
                    if stale_rendered is not None:
                        stale_rendered.final_text = current_text
                        stale_rendered.render_mode = render_mode
                        stale_rendered.used_products = used_products
                        stale_rendered.queue_payload = existing_item.queue_payload
                already_staged_count += 1
                returned_items.append(existing_item)
            continue

        if newly_staged_count >= new_ready_slots:
            continue

        final_text, composition, gpt_meta = _render(contact)
        product_meta = _render_metadata(composition, gpt_meta)

        rendered_message = RenderedMessage(
            campaign_id=campaign_id,
            contact_id=contact.id,
            channel=campaign.channel,
            final_text=final_text,
            render_mode=render_mode,
            used_kb=False,
            used_products=used_products,
            product_snapshot_id=snapshot_id,
            snapshot_expires_at=snapshot_expires_at,
            ready_for_queue=True,
            warnings=None,
        )
        db.add(rendered_message)
        db.flush()

        message = Message(
            campaign_id=campaign_id,
            account_id=assigned_account.id,
            contact_id=contact.id,
            rendered_text=final_text,
            dedupe_key=f"campaign:{campaign_id}:contact:{contact.id}",
        )
        db.add(message)
        db.flush()
        recipient.final_message_id = message.id

        queue_payload = build_staged_queue_payload(
            campaign_id=campaign_id,
            contact_id=contact.id,
            rendered_message_id=rendered_message.id,
            message_id=message.id,
            account_id=message.account_id,
            channel=campaign.channel,
            phone=contact.phone,
            channel_handle=contact.channel_handle,
            final_text=final_text,
            metadata=product_meta,
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
        product_snapshot_valid=bool(used_products or not campaign.include_products),
        force_mock_output=request.force_mock_output,
        real_gpt_called=gpt_called,
        real_queue_push_enabled=settings.REAL_QUEUE_PUSH_ENABLED,
        redis_queue_pushed=False,
        items=[StagedQueueItemResponse.model_validate(item) for item in returned_items],
    )
