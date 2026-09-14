"""Shared tag-audience snapshot. Preview and campaign creation both use resolve_tag_audience."""

from __future__ import annotations

from sqlalchemy.orm import Session

from core_engine.models import CampaignRecipient, RenderStatus, SendStatus
from core_engine.services.contact_tags import resolve_tag_audience


def create_tag_campaign_recipients(
    db: Session,
    *,
    campaign_id: int,
    tags: list[str],
    match: str,
) -> tuple[int, int]:
    """Insert one CampaignRecipient per eligible contact. Returns (created, eligible_count)."""
    eligible, _skipped = resolve_tag_audience(db, tags, match=match)
    created = 0
    for contact in eligible:
        exists = (
            db.query(CampaignRecipient.id)
            .filter(
                CampaignRecipient.campaign_id == int(campaign_id),
                CampaignRecipient.contact_id == int(contact.id),
            )
            .first()
        )
        if exists:
            continue
        db.add(
            CampaignRecipient(
                campaign_id=int(campaign_id),
                contact_id=int(contact.id),
                render_status=RenderStatus.PENDING,
                send_status=SendStatus.PENDING,
            )
        )
        created += 1
    db.flush()
    return created, len(eligible)
