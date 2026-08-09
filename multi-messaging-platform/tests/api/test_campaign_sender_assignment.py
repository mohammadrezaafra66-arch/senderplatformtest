import asyncio
import uuid

import pytest
from fastapi import HTTPException

from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    Message,
    PlatformType,
    RenderedMessage,
    StagedQueueItem,
)
from core_engine.schemas.phase4 import PrepareMessagesRequest
from core_engine.services.campaign_sender_assignment import (
    resolve_campaign_sender_accounts,
)
from core_engine.services.phase4_prepare import prepare_campaign_messages
from core_engine.services import campaign_control


@pytest.fixture
def sender_env(pg_session_factory):
    session = pg_session_factory()
    campaign_ids: list[int] = []
    contact_ids: list[int] = []
    account_ids: list[int] = []

    def campaign(contact_count=4, platform=PlatformType.BALE):
        row = Campaign(
            name=f"sender-{uuid.uuid4().hex}",
            title="sender assignment",
            channel=platform.value,
            platform=platform,
            status=CampaignStatus.DRAFT.value,
            template_text="hello {{first_name}}",
        )
        session.add(row)
        session.flush()
        campaign_ids.append(row.id)
        for index in range(contact_count):
            contact = Contact(
                first_name=str(index),
                phone=f"+98{uuid.uuid4().int % 10**10:010d}",
                consent_status="allowed",
            )
            session.add(contact)
            session.flush()
            contact_ids.append(contact.id)
            session.add(CampaignRecipient(campaign_id=row.id, contact_id=contact.id))
        session.commit()
        return row

    def account(platform=PlatformType.BALE, status=AccountStatus.ACTIVE):
        row = Account(platform=platform, status=status, label=uuid.uuid4().hex)
        session.add(row)
        session.flush()
        account_ids.append(row.id)
        return row

    yield session, campaign, account

    session.rollback()
    if campaign_ids:
        session.query(StagedQueueItem).filter(
            StagedQueueItem.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)
        session.query(RenderedMessage).filter(
            RenderedMessage.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)
        session.query(CampaignRecipient).filter(
            CampaignRecipient.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)
        session.query(Message).filter(Message.campaign_id.in_(campaign_ids)).delete(
            synchronize_session=False
        )
        session.query(CampaignAccount).filter(
            CampaignAccount.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)
        session.query(Campaign).filter(Campaign.id.in_(campaign_ids)).delete(
            synchronize_session=False
        )
    if contact_ids:
        session.query(Contact).filter(Contact.id.in_(contact_ids)).delete(
            synchronize_session=False
        )
    if account_ids:
        session.query(Account).filter(Account.id.in_(account_ids)).delete(
            synchronize_session=False
        )
    session.commit()
    session.close()


def _assignments(session, campaign_id):
    return (
        session.query(Message)
        .join(CampaignRecipient, CampaignRecipient.final_message_id == Message.id)
        .filter(CampaignRecipient.campaign_id == campaign_id)
        .order_by(CampaignRecipient.id.asc())
        .all()
    )


def test_manual_round_robin_priority_disabled_and_persistence(sender_env):
    session, make_campaign, make_account = sender_env
    campaign = make_campaign(contact_count=7)
    first, disabled, second, third = [make_account() for _ in range(4)]
    session.add_all(
        [
            CampaignAccount(campaign_id=campaign.id, account_id=second.id, priority=2),
            CampaignAccount(campaign_id=campaign.id, account_id=first.id, priority=1),
            CampaignAccount(
                campaign_id=campaign.id,
                account_id=disabled.id,
                priority=1,
                enabled=False,
            ),
            CampaignAccount(campaign_id=campaign.id, account_id=third.id, priority=3),
        ]
    )
    session.commit()

    prepare_campaign_messages(session, campaign.id, PrepareMessagesRequest())
    messages = _assignments(session, campaign.id)
    expected = [first.id, second.id, third.id, first.id, second.id, third.id, first.id]
    assert [message.account_id for message in messages] == expected
    staged = session.query(StagedQueueItem).filter_by(campaign_id=campaign.id).all()
    payloads = {item.contact_id: item.queue_payload for item in staged}
    for message in messages:
        assert payloads[message.contact_id]["message_id"] == message.id
        assert payloads[message.contact_id]["account_id"] == message.account_id
    rendered = session.query(RenderedMessage).filter_by(campaign_id=campaign.id).all()
    assert all(row.queue_payload["account_id"] in expected for row in rendered)


def test_manual_single_sender_assigns_every_message(sender_env):
    session, make_campaign, make_account = sender_env
    campaign = make_campaign(contact_count=3)
    account = make_account()
    session.add(CampaignAccount(campaign_id=campaign.id, account_id=account.id))
    session.commit()
    prepare_campaign_messages(session, campaign.id, PrepareMessagesRequest())
    assert [row.account_id for row in _assignments(session, campaign.id)] == [account.id] * 3


def test_auto_round_robin_filters_status_and_platform(sender_env):
    session, make_campaign, make_account = sender_env
    campaign = make_campaign(contact_count=5)
    first, second = make_account(), make_account()
    make_account(status=AccountStatus.RESTING)
    make_account(platform=PlatformType.RUBIKA)
    session.commit()
    prepare_campaign_messages(session, campaign.id, PrepareMessagesRequest())
    assert [row.account_id for row in _assignments(session, campaign.id)] == [
        first.id,
        second.id,
        first.id,
        second.id,
        first.id,
    ]


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("auto_empty", "no_active_sender_account"),
        ("manual_disabled", "no_enabled_campaign_sender"),
        ("manual_inactive", "campaign_sender_inactive"),
        ("manual_mismatch", "campaign_sender_platform_mismatch"),
    ],
)
def test_sender_validation_fails_before_writes(sender_env, case, code):
    session, make_campaign, make_account = sender_env
    campaign = make_campaign(contact_count=2)
    if case == "manual_disabled":
        account = make_account()
        session.add(CampaignAccount(campaign_id=campaign.id, account_id=account.id, enabled=False))
    elif case == "manual_inactive":
        account = make_account(status=AccountStatus.RESTING)
        session.add(CampaignAccount(campaign_id=campaign.id, account_id=account.id))
    elif case == "manual_mismatch":
        account = make_account(platform=PlatformType.RUBIKA)
        session.add(CampaignAccount(campaign_id=campaign.id, account_id=account.id))
    session.commit()

    with pytest.raises(HTTPException) as excinfo:
        prepare_campaign_messages(session, campaign.id, PrepareMessagesRequest())
    assert excinfo.value.detail["code"] == code
    session.rollback()
    assert session.query(Message).filter_by(campaign_id=campaign.id).count() == 0
    assert session.query(StagedQueueItem).filter_by(campaign_id=campaign.id).count() == 0
    assert session.query(RenderedMessage).filter_by(campaign_id=campaign.id).count() == 0


def test_prepare_is_idempotent_and_keeps_assignments(sender_env):
    session, make_campaign, make_account = sender_env
    campaign = make_campaign(contact_count=4)
    make_account()
    make_account()
    session.commit()
    prepare_campaign_messages(session, campaign.id, PrepareMessagesRequest())
    first = [(row.id, row.account_id) for row in _assignments(session, campaign.id)]
    staged_ids = [
        row.id
        for row in session.query(StagedQueueItem)
        .filter_by(campaign_id=campaign.id)
        .order_by(StagedQueueItem.id)
    ]
    prepare_campaign_messages(session, campaign.id, PrepareMessagesRequest())
    assert [(row.id, row.account_id) for row in _assignments(session, campaign.id)] == first
    assert [
        row.id
        for row in session.query(StagedQueueItem)
        .filter_by(campaign_id=campaign.id)
        .order_by(StagedQueueItem.id)
    ] == staged_ids


def test_start_and_debug_prepare_use_the_same_assignment_algorithm(
    sender_env, monkeypatch
):
    session, make_campaign, make_account = sender_env
    debug_campaign = make_campaign(contact_count=4)
    start_campaign = make_campaign(contact_count=4)
    first, second = make_account(), make_account()
    session.commit()

    prepare_campaign_messages(session, debug_campaign.id, PrepareMessagesRequest())
    debug_assignments = [
        row.account_id for row in _assignments(session, debug_campaign.id)
    ]

    async def clear_pause(_campaign_id):
        return None

    monkeypatch.setattr(campaign_control, "clear_campaign_pause", clear_pause)
    asyncio.run(
        campaign_control.start_campaign(
            session, start_campaign, trigger_bridge=False
        )
    )
    start_assignments = [
        row.account_id for row in _assignments(session, start_campaign.id)
    ]
    assert debug_assignments == start_assignments == [
        first.id,
        second.id,
        first.id,
        second.id,
    ]


def test_missing_manual_account_error_is_defined_for_corrupt_relation():
    campaign = Campaign(id=1, platform=PlatformType.BALE)
    link = CampaignAccount(id=1, campaign_id=1, account_id=999, enabled=True, priority=1)

    class Query:
        def __init__(self, rows):
            self.rows = rows
        def filter(self, *_args):
            return self
        def order_by(self, *_args):
            return self
        def all(self):
            return self.rows

    class Session:
        def query(self, model):
            return Query([link] if model is CampaignAccount else [])

    with pytest.raises(HTTPException) as excinfo:
        resolve_campaign_sender_accounts(Session(), campaign)
    assert excinfo.value.detail["code"] == "campaign_sender_missing"
