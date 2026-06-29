"""Query و export گیرندگان کمپین (message logs)."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core_engine.api.schemas import CampaignRecipientItemResponse
from core_engine.models import Campaign, CampaignRecipient, Contact, SendStatus

CSV_EXPORT_MAX_ROWS = 50_000


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
        .join(Contact, CampaignRecipient.contact_id == Contact.id)
        .filter(CampaignRecipient.campaign_id == campaign_id)
    )
    if send_status is not None:
        query = query.filter(CampaignRecipient.send_status == send_status)
    return query


def recipient_to_response(
    recipient: CampaignRecipient,
    contact: Contact,
) -> CampaignRecipientItemResponse:
    return CampaignRecipientItemResponse(
        id=recipient.id,
        campaign_id=recipient.campaign_id,
        contact_id=recipient.contact_id,
        phone=contact.phone_e164 or contact.phone,
        first_name=contact.first_name,
        last_name=contact.last_name,
        render_status=recipient.render_status.value
        if hasattr(recipient.render_status, "value")
        else str(recipient.render_status),
        send_status=recipient.send_status.value
        if hasattr(recipient.send_status, "value")
        else str(recipient.send_status),
        failure_reason=recipient.failure_reason,
        updated_at=recipient.updated_at,
    )


def fetch_campaign_recipient_rows(
    db: Session,
    campaign_id: int,
    *,
    send_status: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[tuple[CampaignRecipient, Contact]], int]:
    get_campaign_or_404(db, campaign_id)
    status_filter = parse_send_status_filter(send_status)
    query = campaign_recipients_base_query(db, campaign_id, status_filter)
    total_count = query.count()

    query = query.order_by(CampaignRecipient.id.asc())
    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)

    return query.all(), total_count


def build_recipients_csv_bytes(
    rows: list[tuple[CampaignRecipient, Contact]],
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

    for recipient, contact in rows:
        item = recipient_to_response(recipient, contact)
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
