from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from core_engine.models import (
    Campaign,
    CampaignRecipient,
    CampaignStatus,
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



def test_finalize_campaign_when_all_recipients_terminal():
    from workers.db import _finalize_campaign_if_terminal

    session = MagicMock(spec=Session)

    campaign = SimpleNamespace(
        status=CampaignStatus.RUNNING.value,
    )

    session.get.return_value = campaign

    session.query.return_value.filter.return_value.all.return_value = [
        (SendStatus.DELIVERED,),
        (SendStatus.READ,),
    ]

    _finalize_campaign_if_terminal(session, 10)

    assert campaign.status == CampaignStatus.COMPLETED.value

    session.get.assert_called_once_with(Campaign, 10)


def test_campaign_does_not_finalize_with_pending_recipient():
    from workers.db import _finalize_campaign_if_terminal

    session = MagicMock(spec=Session)

    campaign = SimpleNamespace(
        status=CampaignStatus.RUNNING.value,
    )

    session.get.return_value = campaign

    session.query.return_value.filter.return_value.all.return_value = [
        (SendStatus.DELIVERED,),
        (SendStatus.PENDING,),
    ]

    _finalize_campaign_if_terminal(session, 11)

    assert campaign.status == CampaignStatus.RUNNING.value

    session.get.assert_called_once_with(Campaign, 11)


def test_paused_campaign_finalizes_when_all_recipients_terminal():
    from workers.db import _finalize_campaign_if_terminal

    session = MagicMock(spec=Session)

    campaign = SimpleNamespace(
        status=CampaignStatus.PAUSED.value,
    )

    session.get.return_value = campaign

    session.query.return_value.filter.return_value.all.return_value = [
        (SendStatus.DELIVERED,),
        (SendStatus.DELIVERED,),
    ]

    _finalize_campaign_if_terminal(session, 13)

    assert campaign.status == CampaignStatus.COMPLETED.value

    session.get.assert_called_once_with(Campaign, 13)


def test_campaign_fails_when_all_terminal_without_success():
    from workers.db import _finalize_campaign_if_terminal

    session = MagicMock(spec=Session)

    campaign = SimpleNamespace(
        status=CampaignStatus.RUNNING.value,
    )

    session.get.return_value = campaign

    session.query.return_value.filter.return_value.all.return_value = [
        (SendStatus.FAILED_PERMANENT,),
        (SendStatus.BLACKLISTED,),
    ]

    _finalize_campaign_if_terminal(session, 12)

    assert campaign.status == CampaignStatus.FAILED.value

    session.get.assert_called_once_with(Campaign, 12)


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
        if model is Campaign:
            return None
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
