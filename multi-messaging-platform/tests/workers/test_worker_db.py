from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from core_engine.models import (
    CampaignRecipient,
    Message,
    RenderedMessage,
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


def test_update_message_attempt_result_does_not_invent_account_id(caplog):
    """A missing account id must never be replaced with another account's id."""
    session = MagicMock(spec=Session)
    recipient = SimpleNamespace(
        send_status=None,
        failure_reason=None,
        final_message_id=None,
    )
    session.query.return_value.filter.return_value.first.return_value = recipient

    rendered = SimpleNamespace(
        campaign_id=10,
        contact_id=20,
        final_text="hello",
        product_snapshot_id=None,
    )

    def get_side_effect(model, object_id):
        if model is Message:
            return None
        if model is RenderedMessage:
            return rendered
        raise AssertionError(f"unexpected model lookup: {model}")

    session.get.side_effect = get_side_effect

    update_message_attempt_result(
        message_id=30,
        attempt_no=1,
        status="delivered",
        platform_message_id="rubika-123",
        campaign_id=10,
        contact_id=20,
        account_id=None,
        success=True,
        db=session,
    )

    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.commit.assert_called_once_with()
    assert recipient.send_status == SendStatus.DELIVERED
    assert recipient.final_message_id is None
    assert "no account_id supplied" in caplog.text
