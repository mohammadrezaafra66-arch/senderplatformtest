import pytest
from sqlalchemy.orm import Session

from core_engine.models import (
    CampaignRecipient,
    SendStatus,
)
from workers.db import update_message_attempt_result


def test_update_message_attempt_result_updates_recipient(recipient_bundle):
    campaign_id, contact_id, session = recipient_bundle

    update_message_attempt_result(
        message_id=f"{campaign_id}:{contact_id}",
        attempt_no=1,
        status="dry_run",
        platform_message_id="dry-123",
        campaign_id=campaign_id,
        contact_id=contact_id,
        success=True,
        db=session,
    )

    session.expire_all()
    recipient = (
        session.query(CampaignRecipient)
        .filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.contact_id == contact_id,
        )
        .first()
    )
    assert recipient is not None
    assert recipient.send_status == SendStatus.DRY_RUN
