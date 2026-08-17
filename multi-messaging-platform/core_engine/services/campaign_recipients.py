"""Query و export گیرندگان کمپین (message logs)."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session, joinedload

from core_engine.api.schemas import (
    CampaignRecipientDetailResponse,
    CampaignRecipientItemResponse,
    CommittedRenderSampleResponse,
    MessageSenderAccountResponse,
)
from core_engine.models import (
    Campaign,
    CampaignRecipient,
    Contact,
    Message,
    MessageAttempt,
    RenderedMessage,
    SendStatus,
    StagedQueueItem,
)
from core_engine.services.campaign_render import (
    excerpt_final_text,
    list_trace_from_payload,
    public_gpt_trace,
    public_product_trace,
)

CSV_EXPORT_MAX_ROWS = 50_000
COMMITTED_SAMPLE_LIMIT = 5


def parse_send_status_filter(send_status: str | None) -> SendStatus | None:
    if send_status is None:
        return None
    try:
        return SendStatus(send_status)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid send_status '{send_status}'.",
        ) from exc


def get_campaign_or_404(db: Session, campaign_id: int) -> Campaign:
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return campaign


def campaign_recipients_base_query(
    db: Session,
    campaign_id: int,
    send_status: SendStatus | None = None,
):
    query = (
        db.query(CampaignRecipient, Contact)
        .options(
            joinedload(CampaignRecipient.final_message).joinedload(Message.account)
        )
        .join(Contact, CampaignRecipient.contact_id == Contact.id)
        .filter(CampaignRecipient.campaign_id == campaign_id)
    )
    if send_status is not None:
        query = query.filter(CampaignRecipient.send_status == send_status)
    return query


def _status_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _persisted_final_text(
    message: Message | None,
    staged_item: StagedQueueItem | None,
) -> str | None:
    if message is not None and message.rendered_text:
        return message.rendered_text
    if staged_item is not None and staged_item.final_text:
        return staged_item.final_text
    payload = staged_item.queue_payload if staged_item is not None else None
    if isinstance(payload, dict):
        raw = payload.get("final_text")
        if raw:
            return str(raw)
    return None


def recipient_to_response(
    recipient: CampaignRecipient,
    contact: Contact,
    staged_item: StagedQueueItem | None = None,
) -> CampaignRecipientItemResponse:
    message = recipient.final_message
    account = message.account if message is not None else None
    payload = staged_item.queue_payload if staged_item is not None else None
    final_text = _persisted_final_text(message, staged_item)
    preview, has_more = excerpt_final_text(final_text)
    trace = list_trace_from_payload(payload)
    return CampaignRecipientItemResponse(
        id=recipient.id,
        campaign_id=recipient.campaign_id,
        contact_id=recipient.contact_id,
        phone=contact.phone_e164 or contact.phone,
        first_name=contact.first_name,
        last_name=contact.last_name,
        render_status=_status_value(recipient.render_status),
        send_status=_status_value(recipient.send_status),
        failure_reason=recipient.failure_reason,
        final_message_id=recipient.final_message_id,
        account_id=message.account_id if message is not None else None,
        sender_account=(
            MessageSenderAccountResponse(
                account_id=account.id,
                label=account.label,
                account_identifier=account.phone_number,
                platform=account.platform,
                status=account.status,
            )
            if account is not None
            else None
        ),
        updated_at=recipient.updated_at,
        final_text_preview=preview or None,
        has_more=has_more,
        has_long_text=has_more,
        use_gpt=trace["use_gpt"] if payload else None,
        include_products=trace["include_products"] if payload else None,
        variation_id=trace["variation_id"],
        product_count=trace["product_count"] if payload else None,
        render_batch_id=trace["render_batch_id"],
        render_version=trace["render_version"],
        final_text_sha256=trace["final_text_sha256"],
    )


def fetch_campaign_recipient_rows(
    db: Session,
    campaign_id: int,
    *,
    send_status: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[tuple[CampaignRecipient, Contact, StagedQueueItem | None]], int]:
    get_campaign_or_404(db, campaign_id)
    status_filter = parse_send_status_filter(send_status)
    count_query = campaign_recipients_base_query(db, campaign_id, status_filter)
    total_count = count_query.count()

    query = (
        db.query(CampaignRecipient, Contact, StagedQueueItem)
        .options(
            joinedload(CampaignRecipient.final_message).joinedload(Message.account)
        )
        .join(Contact, CampaignRecipient.contact_id == Contact.id)
        .outerjoin(
            StagedQueueItem,
            and_(
                StagedQueueItem.campaign_id == CampaignRecipient.campaign_id,
                StagedQueueItem.contact_id == CampaignRecipient.contact_id,
            ),
        )
        .filter(CampaignRecipient.campaign_id == campaign_id)
    )
    if status_filter is not None:
        query = query.filter(CampaignRecipient.send_status == status_filter)
    query = query.order_by(CampaignRecipient.id.asc())
    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)

    return query.all(), total_count


def fetch_recipient_detail(
    db: Session,
    campaign_id: int,
    recipient_id: int,
) -> CampaignRecipientDetailResponse:
    get_campaign_or_404(db, campaign_id)
    row = (
        db.query(CampaignRecipient, Contact, StagedQueueItem)
        .options(
            joinedload(CampaignRecipient.final_message).joinedload(Message.account),
        )
        .join(Contact, CampaignRecipient.contact_id == Contact.id)
        .outerjoin(
            StagedQueueItem,
            and_(
                StagedQueueItem.campaign_id == CampaignRecipient.campaign_id,
                StagedQueueItem.contact_id == CampaignRecipient.contact_id,
            ),
        )
        .filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.id == recipient_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Recipient not found.")
    recipient, contact, staged = row
    base = recipient_to_response(recipient, contact, staged)
    message = recipient.final_message
    payload = staged.queue_payload if staged is not None else None
    meta = payload.get("metadata") if isinstance(payload, dict) else None
    if not isinstance(meta, dict):
        meta = {}
    final_text = _persisted_final_text(message, staged)
    preview, has_more = excerpt_final_text(final_text)
    latest_attempt = None
    if message is not None:
        latest_attempt = (
            db.query(MessageAttempt)
            .filter(MessageAttempt.message_id == message.id)
            .order_by(MessageAttempt.attempt_no.desc())
            .first()
        )
    rendered_at = None
    if staged is not None and staged.rendered_message_id:
        rendered = db.get(RenderedMessage, staged.rendered_message_id)
        if rendered is not None:
            rendered_at = rendered.created_at
            if not final_text:
                final_text = rendered.final_text
    sent_at = None
    if latest_attempt is not None:
        sent_at = latest_attempt.accepted_at or latest_attempt.started_at
    gpt = public_gpt_trace(meta)
    products = public_product_trace(meta, include_snapshot=True)
    platform = None
    if message is not None and message.account is not None:
        platform = (
            message.account.platform.value
            if hasattr(message.account.platform, "value")
            else str(message.account.platform)
        )
    elif staged is not None:
        platform = staged.channel
    payload = {
        **base.model_dump(),
        "final_text_preview": preview or None,
        "has_more": has_more,
        "has_long_text": has_more,
        "rendered_message_id": (
            staged.rendered_message_id if staged is not None else None
        ),
        "message_id": message.id if message is not None else recipient.final_message_id,
        "attempt_no": latest_attempt.attempt_no if latest_attempt is not None else None,
        "platform": platform,
        "final_text": final_text,
        "gpt": gpt,
        "products": products,
        "error_code": (
            latest_attempt.error_code
            if latest_attempt is not None
            else recipient.failure_reason
        ),
        "rendered_at": rendered_at,
        "sent_at": sent_at,
        "created_at": recipient.updated_at,
    }
    return CampaignRecipientDetailResponse.model_validate(payload)


def fetch_committed_render_samples(
    db: Session,
    campaign_id: int,
    *,
    limit: int = COMMITTED_SAMPLE_LIMIT,
) -> tuple[list[CommittedRenderSampleResponse], str | None]:
    rows = (
        db.query(RenderedMessage, Contact)
        .outerjoin(Contact, RenderedMessage.contact_id == Contact.id)
        .filter(RenderedMessage.campaign_id == campaign_id)
        .order_by(RenderedMessage.id.asc())
        .limit(limit)
        .all()
    )
    samples: list[CommittedRenderSampleResponse] = []
    latest_batch = None
    for rendered, contact in rows:
        payload = rendered.queue_payload if isinstance(rendered.queue_payload, dict) else {}
        trace = list_trace_from_payload(payload)
        latest_batch = trace["render_batch_id"] or latest_batch
        name_parts = []
        if contact is not None:
            name_parts = [contact.first_name, contact.last_name]
        samples.append(
            CommittedRenderSampleResponse(
                rendered_message_id=rendered.id,
                contact_id=rendered.contact_id,
                recipient_name=" ".join(part for part in name_parts if part) or None,
                sender_account_id=payload.get("account_id"),
                final_text=rendered.final_text,
                final_text_sha256=trace["final_text_sha256"],
                render_batch_id=trace["render_batch_id"],
                render_version=trace["render_version"],
                use_gpt=bool(trace["use_gpt"]),
                variation_id=trace["variation_id"],
                include_products=bool(trace["include_products"]),
                product_count=int(trace["product_count"] or 0),
                rendered_at=rendered.created_at,
            )
        )
    return samples, latest_batch


def build_recipients_csv_bytes(
    rows: list,
) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "campaign_id",
            "contact_id",
            "phone",
            "first_name",
            "last_name",
            "render_status",
            "send_status",
            "updated_at",
        ]
    )

    for row in rows:
        recipient, contact = row[0], row[1]
        staged = row[2] if len(row) > 2 else None
        item = recipient_to_response(recipient, contact, staged)
        updated_at = item.updated_at
        if isinstance(updated_at, datetime):
            updated_text = updated_at.isoformat()
        else:
            updated_text = str(updated_at)

        writer.writerow(
            [
                item.id,
                item.campaign_id,
                item.contact_id,
                item.phone or "",
                item.first_name or "",
                item.last_name or "",
                item.render_status,
                item.send_status,
                updated_text,
            ]
        )

    # UTF-8 BOM helps Excel open Persian text correctly on Windows.
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def export_filename(campaign_id: int) -> str:
    return f"campaign_{campaign_id}_recipients.csv"
