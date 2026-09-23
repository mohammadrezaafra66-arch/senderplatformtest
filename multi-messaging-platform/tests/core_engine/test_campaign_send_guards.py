"""Hermetic send guards. No connector call and no live campaign."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread

import pytest
from zoneinfo import ZoneInfo

from core_engine.models import (
    CampaignStatus,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.schemas.phase4 import PrepareMessagesRequest
from core_engine.services.campaign_capacity import SendWindowSpec
from core_engine.services.campaign_send_safety import (
    PILOT_SUCCESS_LIMIT,
    STOP_STOPPED,
    STOP_STOPPING,
    confirm_pilot_resume,
    confirm_pilot_success,
    ensure_pilot,
    load_or_refresh_shared_snapshot,
    logical_send_key,
    pause_all_running,
    pilot_blocks_start,
    pre_connector_decision,
    record_definitive_success,
    release_pilot_permit,
    reserve_pilot_permit,
    stop_progress_label,
    success_count,
    window_is_open,
)
from core_engine.services.campaign_staging_window import (
    REFILL_TARGET,
    STAGING_HIGH_WATER,
    STAGING_LOW_WATER,
    count_active_staged,
    refill_campaign_staging,
    refill_deficit,
)
from core_engine.services.phase4_prepare import PREPARE_BATCH_SIZE, prepare_campaign_messages
from core_engine.models import CampaignPilotState
from core_engine.services.product_feed.errors import ProductFeedError
from core_engine.services.product_feed.fake_provider import FakeProductFeedProvider
from core_engine.services.product_feed.send_time_refresh import (
    PRODUCT_SNAPSHOT_MAX_AGE_SECONDS,
    render_outbound_from_selection,
)
from tests.core_engine.test_production_safety_guards import _campaign, _contact, _make_account
from tests.core_engine.test_send_time_product_refresh import _payload, _row

IRAN = ZoneInfo("Asia/Tehran")


def test_prepare_batch_400_is_a_flush_not_a_ready_cap():
    assert PREPARE_BATCH_SIZE == 400
    assert STAGING_HIGH_WATER == 500
    assert STAGING_LOW_WATER == 200
    assert REFILL_TARGET == 500
    assert refill_deficit(199) == 301
    assert refill_deficit(200) == 0
    assert refill_deficit(500) == 0
    assert refill_deficit(0) == 500


def test_reprepare_and_refill_keep_the_window(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="window")
    contacts = [_contact(session, valid=True, first_name=f"W{i}") for i in range(6)]
    campaign = _campaign(session, account, contacts, template="متن ثابت")
    prepare_campaign_messages(
        session, campaign.id, PrepareMessagesRequest(force_mock_output=False)
    )
    ready = (
        session.query(StagedQueueItem)
        .filter_by(campaign_id=campaign.id, status=StagedQueueItemStatus.READY.value)
        .order_by(StagedQueueItem.id.asc())
        .all()
    )
    assert len(ready) == 6
    assert len(ready) <= STAGING_HIGH_WATER
    assert [int(item.contact_id) for item in ready] == [int(c.id) for c in contacts]
    campaign.status = CampaignStatus.RUNNING.value
    session.commit()
    held = refill_campaign_staging(session, campaign.id)
    assert held["added"] == 0 or held["reason"] == "refilled"
    session.expire_all()
    assert count_active_staged(session, campaign.id) == 6

    for item in ready[:5]:
        item.status = StagedQueueItemStatus.SKIPPED.value
    session.commit()
    # 1 ready is below 200, but every remaining recipient is already staged.
    again = refill_campaign_staging(session, campaign.id)
    session.expire_all()
    assert count_active_staged(session, campaign.id) <= STAGING_HIGH_WATER
    assert again["added"] == 0


def test_two_hundred_active_rows_do_not_refill(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="low-water")
    contacts = [_contact(session, valid=True) for _ in range(3)]
    campaign = _campaign(session, account, contacts, template="متن ثابت")
    session.commit()
    for contact in contacts:
        session.add(
            StagedQueueItem(
                campaign_id=campaign.id,
                contact_id=contact.id,
                channel="rubika",
                status=StagedQueueItemStatus.READY.value,
                final_text="متن",
                queue_payload={"campaign_id": campaign.id, "contact_id": contact.id},
            )
        )
    # Pad the active count to the real low-water mark without rendering 200 messages.
    pad_contact = contacts[0]
    for _ in range(STAGING_LOW_WATER - 3):
        session.add(
            StagedQueueItem(
                campaign_id=campaign.id,
                contact_id=pad_contact.id,
                channel="rubika",
                status=StagedQueueItemStatus.QUEUED.value,
                final_text="متن",
                queue_payload={"campaign_id": campaign.id},
            )
        )
    session.commit()
    assert count_active_staged(session, campaign.id) == STAGING_LOW_WATER
    result = refill_campaign_staging(session, campaign.id)
    assert result["reason"] == "not_running" or result["added"] == 0
    campaign.status = CampaignStatus.RUNNING.value
    session.commit()
    result = refill_campaign_staging(session, campaign.id)
    assert result["added"] == 0
    assert result["reason"] == "above_low_water"


def test_stop_and_window_and_counter_and_pilot(pg_session_factory):
    assert stop_progress_label(2) == STOP_STOPPING
    assert stop_progress_label(0) == STOP_STOPPED
    assert pre_connector_decision(
        kill_switch=True, campaign_paused=False, window_open=True, already_submitted=False
    ) == "stop"
    assert pre_connector_decision(
        kill_switch=False, campaign_paused=False, window_open=True, already_submitted=True
    ) == "inflight_submitted"
    assert pre_connector_decision(
        kill_switch=False, campaign_paused=False, window_open=False, already_submitted=False
    ) == "defer"

    overnight = [SendWindowSpec(phase="night", start_hour=22, end_hour=6)]
    closed, nxt = window_is_open(overnight, datetime(2026, 9, 23, 12, 0, tzinfo=IRAN))
    assert closed is False
    assert nxt is not None
    opened, _ = window_is_open(overnight, datetime(2026, 9, 23, 23, 0, tzinfo=IRAN))
    assert opened is True

    session = pg_session_factory()
    account = _make_account(session, label="ledger")
    contact = _contact(session, valid=True)
    campaign = _campaign(session, account, [contact], template="متن ثابت")
    session.commit()
    assert logical_send_key(campaign.id, contact.id) == f"campaign:{campaign.id}:contact:{contact.id}"
    assert record_definitive_success(
        session, campaign_id=campaign.id, contact_id=contact.id, message_id=None
    )
    assert record_definitive_success(
        session, campaign_id=campaign.id, contact_id=contact.id, message_id=None
    ) is False
    assert success_count(session, campaign.id) == 1
    session.commit()

    ensure_pilot(session, campaign.id)
    session.commit()
    for _ in range(PILOT_SUCCESS_LIMIT):
        assert reserve_pilot_permit(session, campaign.id) is True
        confirm_pilot_success(session, campaign.id)
        session.commit()
    assert reserve_pilot_permit(session, campaign.id) is False
    session.refresh(campaign)
    assert campaign.status == CampaignStatus.PAUSED.value
    assert pilot_blocks_start(session, campaign.id) is True
    confirm_pilot_resume(session, campaign.id)
    session.commit()
    assert pilot_blocks_start(session, campaign.id) is False

    other = _campaign(session, account, [contact], template="متن ثابت")
    other.status = CampaignStatus.RUNNING.value
    session.commit()
    assert pause_all_running(session) >= 1
    session.refresh(other)
    assert other.status == CampaignStatus.PAUSED.value

    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [{"id": "A"}]

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    load_or_refresh_shared_snapshot(session, fetch, now=now)
    load_or_refresh_shared_snapshot(session, fetch, now=now + timedelta(seconds=10))
    assert calls["n"] == 1
    load_or_refresh_shared_snapshot(session, fetch, now=now + timedelta(seconds=31))
    assert calls["n"] == 2
    session.commit()

    def boom():
        raise ProductFeedError("PRODUCT_REFRESH_FAILED", "down")

    with pytest.raises(ProductFeedError):
        load_or_refresh_shared_snapshot(
            session, boom, now=now + timedelta(seconds=90)
        )

    blocked = render_outbound_from_selection(
        _payload(["A"]),
        provider=FakeProductFeedProvider([
            _row("A", tags=["تبلیغات"], available=False, cash=11_000_000),
        ]),
    )
    assert blocked.blocked is True
    assert PRODUCT_SNAPSHOT_MAX_AGE_SECONDS == 30


def test_concurrent_refill_does_not_duplicate_recipients(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="race")
    contacts = [_contact(session, valid=True) for _ in range(4)]
    campaign = _campaign(session, account, contacts, template="متن ثابت")
    campaign.status = CampaignStatus.RUNNING.value
    session.commit()
    campaign_id = campaign.id
    barrier = Barrier(2)
    errors: list[BaseException] = []

    def _run() -> None:
        worker = pg_session_factory()
        try:
            barrier.wait(timeout=15)
            refill_campaign_staging(worker, campaign_id)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            worker.close()

    threads = [Thread(target=_run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert thread.is_alive() is False
    assert errors == []
    session.expire_all()
    rows = (
        session.query(StagedQueueItem)
        .filter_by(campaign_id=campaign_id, status=StagedQueueItemStatus.READY.value)
        .all()
    )
    assert len(rows) == 4
    assert len({int(row.contact_id) for row in rows}) == 4
    assert len(rows) <= STAGING_HIGH_WATER


def test_failed_pilot_permit_is_released(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="release")
    contact = _contact(session, valid=True)
    campaign = _campaign(session, account, [contact], template="متن ثابت")
    ensure_pilot(session, campaign.id)
    session.commit()
    assert reserve_pilot_permit(session, campaign.id) is True
    release_pilot_permit(session, campaign.id)
    session.commit()
    row_reserved = session.get(CampaignPilotState, campaign.id)
    assert row_reserved.reserved == 0
    assert reserve_pilot_permit(session, campaign.id) is True


def _bulk_campaign(session, account, count: int):
    from core_engine.models import CampaignRecipient, Contact, RenderStatus, SendStatus

    contacts = []
    for index in range(count):
        phone = f"+989{index:09d}"
        contacts.append(
            Contact(
                phone=phone,
                phone_e164=phone,
                first_name=f"N{index}",
                consent_status="allowed",
                blacklisted=False,
            )
        )
    session.add_all(contacts)
    session.flush()
    campaign = _campaign(session, account, [], template="متن ثابت")
    session.add_all(
        [
            CampaignRecipient(
                campaign_id=campaign.id,
                contact_id=contact.id,
                render_status=RenderStatus.PENDING,
                send_status=SendStatus.PENDING,
            )
            for contact in contacts
        ]
    )
    session.commit()
    return campaign


def _recipient_contact_ids(session, campaign_id: int) -> list[int]:
    from core_engine.models import CampaignRecipient

    return [
        int(row[0])
        for row in session.query(CampaignRecipient.contact_id)
        .filter(CampaignRecipient.campaign_id == campaign_id)
        .order_by(CampaignRecipient.id.asc())
        .all()
    ]


def _active_contact_ids(session, campaign_id: int) -> list[int]:
    rows = (
        session.query(StagedQueueItem.contact_id)
        .filter(
            StagedQueueItem.campaign_id == campaign_id,
            StagedQueueItem.status.in_(("ready", "queued", "pushing")),
        )
        .order_by(StagedQueueItem.id.asc())
        .all()
    )
    return [int(row[0]) for row in rows]


def test_real_refill_window_on_pytest_database(pg_session_factory):
    """600 recipients, 199 active, worker refill path, then 200 and concurrency."""
    from threading import Barrier, Thread

    session = pg_session_factory()
    account = _make_account(session, label="real-window")
    campaign = _bulk_campaign(session, account, 600)
    assert len(_recipient_contact_ids(session, campaign.id)) == 600
    prepare_campaign_messages(
        session,
        campaign.id,
        PrepareMessagesRequest(limit=199, force_mock_output=False),
    )
    campaign.status = CampaignStatus.RUNNING.value
    session.commit()
    session.expire_all()
    before_ids = _active_contact_ids(session, campaign.id)
    assert len(before_ids) == 199
    assert len(set(before_ids)) == 199
    ordered = _recipient_contact_ids(session, campaign.id)
    assert before_ids == ordered[:199]

    first = refill_campaign_staging(session, campaign.id)
    session.expire_all()
    after_ids = _active_contact_ids(session, campaign.id)
    assert first["added"] == 301
    assert len(after_ids) == 500
    assert len(set(after_ids)) == 500
    assert after_ids == ordered[:500]
    assert after_ids[199:] == ordered[199:500]

    second = refill_campaign_staging(session, campaign.id)
    session.expire_all()
    assert second["added"] == 0
    assert len(_active_contact_ids(session, campaign.id)) == 500

    barrier = Barrier(2)
    errors: list[BaseException] = []

    def _worker() -> None:
        worker = pg_session_factory()
        try:
            barrier.wait(timeout=30)
            refill_campaign_staging(worker, campaign.id)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            worker.close()

    threads = [Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
        assert thread.is_alive() is False
    assert errors == []
    session.expire_all()
    assert len(_active_contact_ids(session, campaign.id)) == 500

    newest = (
        session.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == campaign.id,
            StagedQueueItem.status == StagedQueueItemStatus.READY.value,
        )
        .order_by(StagedQueueItem.id.desc())
        .limit(300)
        .all()
    )
    for item in newest:
        item.status = StagedQueueItemStatus.SKIPPED.value
    session.commit()
    session.expire_all()
    assert len(_active_contact_ids(session, campaign.id)) == 200
    held = refill_campaign_staging(session, campaign.id)
    session.expire_all()
    assert held["added"] == 0
    assert held["reason"] == "above_low_water"
    assert len(_active_contact_ids(session, campaign.id)) == 200


def test_send_guards_migration_up_and_down_on_pytest_database(pg_session_factory):
    import importlib.util
    from pathlib import Path

    import alembic.op as op_mod
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    from core_engine.database import Base
    from tests.isolation import assert_pytest_database_name, rewrite_database_url

    session = pg_session_factory()
    source = session.get_bind().engine.url.render_as_string(hide_password=False)
    target_name = "mmp_pytest_send_guards_mig"
    assert_pytest_database_name(target_name)
    admin = create_engine(rewrite_database_url(source, "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        current = conn.execute(text("SELECT current_database()")).scalar()
        assert str(current) == "postgres"
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": target_name},
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target_name}"'))
    admin.dispose()

    engine = create_engine(rewrite_database_url(source, target_name))
    with engine.connect() as conn:
        assert assert_pytest_database_name(
            str(conn.execute(text("SELECT current_database()")).scalar())
        ) == target_name
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS product_send_snapshots"))
        conn.execute(text("DROP TABLE IF EXISTS campaign_pilot_states"))
        conn.execute(text("DROP TABLE IF EXISTS campaign_send_successes"))

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "campaign_send_guards_001.py"
    )
    spec = importlib.util.spec_from_file_location("campaign_send_guards_001", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def _run(fn) -> None:
        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            operations = Operations(ctx)
            operations._install_proxy()
            try:
                fn()
            finally:
                operations._remove_proxy()

    def _tables() -> set[str]:
        return set(inspect(engine).get_table_names())

    _run(module.upgrade)
    assert {"campaign_send_successes", "campaign_pilot_states", "product_send_snapshots"} <= _tables()
    _run(module.downgrade)
    names = _tables()
    assert "campaign_send_successes" not in names
    assert "campaign_pilot_states" not in names
    assert "product_send_snapshots" not in names
    _run(module.upgrade)
    assert "campaign_send_successes" in _tables()
    engine.dispose()
