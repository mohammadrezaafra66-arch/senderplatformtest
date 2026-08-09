"""Staged rows without a verifiable rendered payload must never be sent.

Ten such rows exist in the field: staged outside the prepare path, so
``rendered_message_id`` is NULL and their text cannot be traced to a
RenderedMessage. The bridge must ignore them rather than push whatever text
they happen to carry.
"""

import asyncio
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from core_engine.database import Base
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignStatus,
    Contact,
    Message,
    PlatformType,
    RenderedMessage,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.services import queue_bridge

DRY_RUN_MARKER = "این پیام فقط تست dry-run است"


def _postgres_url() -> str | None:
    url = os.getenv("DATABASE_URL")
    return url if url and url.startswith("postgresql") else None


@pytest.fixture
def pg_engine():
    url = _postgres_url()
    if not url:
        pytest.skip("DATABASE_URL not set for Postgres-backed bridge tests")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Postgres not reachable for bridge tests")
    yield engine
    engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine):
    Base.metadata.create_all(pg_engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)


class FakeRedis:
    def __init__(self):
        self.pushed: list[tuple[str, str]] = []

    async def rpush(self, key, value):
        self.pushed.append((key, value))
        return 1


@pytest.fixture
def bridge_env(pg_session_factory, monkeypatch):
    session = pg_session_factory()
    fake_redis = FakeRedis()
    monkeypatch.setattr(queue_bridge, "get_redis_client", lambda: fake_redis)

    class _Settings:
        REAL_QUEUE_PUSH_ENABLED = True
        WHATSAPP_DELIVERY_MODE = "web"

    monkeypatch.setattr(queue_bridge, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        queue_bridge, "get_consent_block_reason", lambda *a, **k: None
    )

    campaign = Campaign(
        name="bridge",
        title="bridge",
        channel="telegram",
        platform=PlatformType.TELEGRAM,
        template_text="سلام {{first_name}}",
        status=CampaignStatus.RUNNING.value,
    )
    account = Account(
        platform=PlatformType.TELEGRAM,
        phone_number="+989120007001",
        label="bridge acct",
        status=AccountStatus.ACTIVE,
    )
    session.add_all([campaign, account])
    session.flush()

    created = {"campaign": campaign, "account": account, "contacts": []}

    def _contact(name, phone):
        contact = Contact(
            first_name=name,
            phone=phone,
            phone_e164=phone,
            consent_status="allowed",
        )
        session.add(contact)
        session.flush()
        created["contacts"].append(contact.id)
        return contact

    def _staged(contact, *, text, with_rendered):
        rendered_id = None
        if with_rendered:
            rendered = RenderedMessage(
                campaign_id=campaign.id,
                contact_id=contact.id,
                channel="telegram",
                final_text=text,
                render_mode="template",
                used_kb=False,
                used_products=False,
                ready_for_queue=True,
            )
            session.add(rendered)
            session.flush()
            rendered_id = rendered.id

        message = None
        if with_rendered:
            message = Message(
                campaign_id=campaign.id,
                account_id=account.id,
                contact_id=contact.id,
                rendered_text=text,
                dedupe_key=f"bridge:{campaign.id}:{contact.id}",
            )
            session.add(message)
            session.flush()

        item = StagedQueueItem(
            campaign_id=campaign.id,
            contact_id=contact.id,
            rendered_message_id=rendered_id,
            channel="telegram",
            status=StagedQueueItemStatus.READY.value,
            final_text=text,
            queue_payload={
                "campaign_id": campaign.id,
                "contact_id": contact.id,
                "message_id": message.id if message is not None else None,
                "account_id": account.id if message is not None else None,
                "channel": "telegram",
                "phone": contact.phone,
                "final_text": text,
            },
        )
        session.add(item)
        session.flush()
        return item

    session.commit()
    yield session, fake_redis, campaign, _contact, _staged

    session.query(StagedQueueItem).filter(
        StagedQueueItem.campaign_id == campaign.id
    ).delete(synchronize_session=False)
    session.query(RenderedMessage).filter(
        RenderedMessage.campaign_id == campaign.id
    ).delete(synchronize_session=False)
    session.query(Message).filter(Message.campaign_id == campaign.id).delete(
        synchronize_session=False
    )
    for contact_id in created["contacts"]:
        session.query(Contact).filter(Contact.id == contact_id).delete(
            synchronize_session=False
        )
    session.query(Campaign).filter(Campaign.id == campaign.id).delete(
        synchronize_session=False
    )
    session.query(Account).filter(Account.id == account.id).delete(
        synchronize_session=False
    )
    session.commit()
    session.close()


def test_item_without_rendered_message_is_never_pushed(bridge_env):
    """Test 1 — an orphan ready row is not claimed and not sent."""
    session, redis, campaign, contact, staged = bridge_env
    orphan = staged(
        contact("مهرداد", "+989120007101"),
        text=f"مهرداد عزیز، چیزی. {DRY_RUN_MARKER}",
        with_rendered=False,
    )
    session.commit()

    result = asyncio.run(queue_bridge.push_staged_items_to_worker_queue(session))

    assert result["pushed"] == 0
    assert redis.pushed == []

    session.expire_all()
    orphan = session.get(StagedQueueItem, orphan.id)
    # Test 2 of the brief — status untouched, not silently sent or failed.
    assert orphan.status == StagedQueueItemStatus.READY.value
    assert orphan.skip_reason is None


def test_item_with_rendered_message_is_still_pushed(bridge_env):
    """Test 2 — a properly prepared row keeps flowing."""
    session, redis, campaign, contact, staged = bridge_env
    good = staged(
        contact("مطهره", "+989120007102"),
        text="سلام مطهره",
        with_rendered=True,
    )
    session.commit()

    result = asyncio.run(queue_bridge.push_staged_items_to_worker_queue(session))

    assert result["pushed"] == 1
    assert len(redis.pushed) == 1
    assert redis.pushed[0][0] == f"queue:telegram:{good.queue_payload['account_id']}"
    assert "سلام مطهره" in redis.pushed[0][1]

    session.expire_all()
    assert (
        session.get(StagedQueueItem, good.id).status
        == StagedQueueItemStatus.QUEUED.value
    )


def test_orphan_does_not_block_the_valid_item_behind_it(bridge_env):
    """Test 3 — one bad row must not stall the queue."""
    session, redis, campaign, contact, staged = bridge_env
    orphan = staged(
        contact("مهرداد", "+989120007103"),
        text=f"چیز قدیمی. {DRY_RUN_MARKER}",
        with_rendered=False,
    )
    good = staged(
        contact("مطهره", "+989120007104"),
        text="سلام مطهره",
        with_rendered=True,
    )
    session.commit()
    assert orphan.id < good.id, "orphan must sort first to prove it is skipped"

    result = asyncio.run(queue_bridge.push_staged_items_to_worker_queue(session))

    assert result["pushed"] == 1
    assert len(redis.pushed) == 1

    session.expire_all()
    assert (
        session.get(StagedQueueItem, good.id).status
        == StagedQueueItemStatus.QUEUED.value
    )
    assert (
        session.get(StagedQueueItem, orphan.id).status
        == StagedQueueItemStatus.READY.value
    )


def test_sender_assignment_mismatch_is_never_pushed(bridge_env):
    session, redis, _campaign, contact, staged = bridge_env
    item = staged(
        contact("mismatch", "+989120007105"),
        text="sender mismatch",
        with_rendered=True,
    )
    item.queue_payload = {
        **item.queue_payload,
        "account_id": int(item.queue_payload["account_id"]) + 1_000_000,
    }
    session.commit()

    result = asyncio.run(queue_bridge.push_staged_items_to_worker_queue(session))

    assert result["pushed"] == 0
    assert result["skipped_invalid"] == 1
    assert redis.pushed == []
    session.expire_all()
    refreshed = session.get(StagedQueueItem, item.id)
    assert refreshed.status == StagedQueueItemStatus.SKIPPED.value
    assert refreshed.skip_reason == "invalid_payload:sender_assignment_mismatch"


def test_no_dry_run_text_is_ever_pushed(bridge_env):
    """Test 4 — the placeholder text is never used as a fallback."""
    session, redis, campaign, contact, staged = bridge_env
    staged(
        contact("مهرداد", "+989120007105"),
        text=f"مهرداد عزیز، چند محصول. {DRY_RUN_MARKER}",
        with_rendered=False,
    )
    session.commit()

    asyncio.run(queue_bridge.push_staged_items_to_worker_queue(session))

    assert all(DRY_RUN_MARKER not in payload for _key, payload in redis.pushed)
    assert redis.pushed == []


def test_empty_rendered_text_is_parked_not_sent(bridge_env):
    """A claimed row whose payload has no text is skipped with a reason."""
    session, redis, campaign, contact, staged = bridge_env
    blank = staged(
        contact("خالی", "+989120007106"),
        text="",
        with_rendered=True,
    )
    blank.queue_payload = {**blank.queue_payload, "final_text": "   "}
    session.commit()

    result = asyncio.run(queue_bridge.push_staged_items_to_worker_queue(session))

    assert result["pushed"] == 0
    assert result["skipped_invalid"] == 1
    assert redis.pushed == []

    session.expire_all()
    blank = session.get(StagedQueueItem, blank.id)
    assert blank.status == StagedQueueItemStatus.SKIPPED.value
    assert blank.skip_reason == "invalid_payload:missing_rendered_text"
