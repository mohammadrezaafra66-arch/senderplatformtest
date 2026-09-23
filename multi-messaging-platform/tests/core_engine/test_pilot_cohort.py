"""Frozen pilot cohort. No live database and no connector call."""

from __future__ import annotations

from threading import Barrier, Thread

import pytest

from core_engine.models import (
    CampaignPilotCohortMember,
    CampaignRecipient,
    CampaignStatus,
    MessageAttempt,
    RenderedMessage,
    RenderStatus,
    SendStatus,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.schemas.phase4 import PrepareMessagesRequest
from core_engine.services.campaign_dispatch import claim_ready_items
from core_engine.services.campaign_pilot_cohort import (
    COHORT_TERMINAL_SEND_STATUSES,
    admit_connector_call,
    configure_pilot_recipient_limit,
    connector_admission,
    materialize_pilot_cohort,
    pause_if_cohort_exhausted,
    reset_pilot_cohort,
)
from core_engine.services.campaign_reprepare import reprepare_campaign
from core_engine.services.campaign_send_safety import (
    confirm_pilot_success,
    record_definitive_success,
    success_count,
)
from core_engine.services.campaign_staging_window import STAGING_HIGH_WATER
from core_engine.services.phase4_prepare import prepare_campaign_messages
from core_engine.services.queue_bridge import emergency_stop_allows_claim
from tests.core_engine.test_production_safety_guards import _campaign, _contact, _make_account
from workers.db import _TERMINAL_CAMPAIGN_SEND_STATUSES


@pytest.fixture(autouse=True)
def _sync_pytest_cohort_columns(request):
    """create_all does not add columns to a database created by an older model."""
    if "pg_engine" not in request.fixturenames and "pg_session_factory" not in request.fixturenames:
        return
    engine = request.getfixturevalue("pg_engine")
    from sqlalchemy import text

    from core_engine.database import Base

    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE campaign_pilot_states "
                "ADD COLUMN IF NOT EXISTS recipient_limit INTEGER"
            )
        )
    Base.metadata.create_all(engine)


def _quiet(_campaign_id: int):
    return None


def _recipients(session, campaign_id: int) -> list[CampaignRecipient]:
    return (
        session.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == campaign_id)
        .order_by(CampaignRecipient.id.asc())
        .all()
    )


def _stage_ready(session, campaign, contact) -> None:
    rendered = RenderedMessage(
        campaign_id=campaign.id,
        contact_id=contact.id,
        channel="rubika",
        final_text="سلام",
        render_mode="template",
        used_kb=False,
        used_products=False,
        ready_for_queue=True,
    )
    session.add(rendered)
    session.flush()
    session.add(
        StagedQueueItem(
            campaign_id=campaign.id,
            contact_id=contact.id,
            rendered_message_id=rendered.id,
            channel="rubika",
            status=StagedQueueItemStatus.READY.value,
            final_text="سلام",
            queue_payload={"final_text": "سلام", "message_id": rendered.id},
        )
    )
    session.flush()


def _arm(session, campaign, limit: int) -> list[CampaignRecipient]:
    configure_pilot_recipient_limit(session, campaign.id, limit)
    materialize_pilot_cohort(session, campaign.id)
    session.commit()
    return _recipients(session, campaign.id)


def _claim(session, campaign_id: int, batch: int) -> list[int]:
    rows = claim_ready_items(
        session,
        campaign_ids=[campaign_id],
        batch_size=batch,
        per_campaign=batch,
        blocked_account_ids=set(),
    )
    return [int(row.contact_id) for row in rows]


def test_cohort_is_the_first_100_and_blocks_rank_101(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="cohort-100")
    contacts = [_contact(session, first_name=f"C{i}") for i in range(101)]
    campaign = _campaign(session, account, contacts, template="متن ثابت")
    rows = _arm(session, campaign, 100)
    members = (
        session.query(CampaignPilotCohortMember)
        .filter_by(campaign_id=campaign.id)
        .order_by(CampaignPilotCohortMember.ordinal.asc())
        .all()
    )
    assert [member.ordinal for member in members] == list(range(1, 101))
    assert [member.campaign_recipient_id for member in members] == [row.id for row in rows[:100]]
    assert rows[100].id not in {member.campaign_recipient_id for member in members}
    assert len({(member.campaign_id, member.campaign_recipient_id) for member in members}) == 100
    assert len({(member.campaign_id, member.ordinal) for member in members}) == 100


def test_failure_cannot_claim_rank_101(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="fail-101")
    contacts = [_contact(session, first_name=f"F{i}") for i in range(101)]
    campaign = _campaign(session, account, contacts, template="متن ثابت")
    rows = _arm(session, campaign, 100)
    for contact in contacts:
        _stage_ready(session, campaign, contact)
    rows[0].send_status = SendStatus.FAILED_PERMANENT
    campaign.status = CampaignStatus.RUNNING.value
    session.commit()
    claimed = _claim(session, campaign.id, 101)
    assert contacts[100].id not in claimed
    assert set(claimed) <= {contact.id for contact in contacts[:100]}
    outside = (
        session.query(StagedQueueItem)
        .filter_by(campaign_id=campaign.id, contact_id=contacts[100].id)
        .one()
    )
    assert outside.status == StagedQueueItemStatus.READY.value
    assert STAGING_HIGH_WATER == 500


def test_later_failures_stay_inside_the_cohort(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="fail-many")
    contacts = [_contact(session, first_name=f"M{i}") for i in range(8)]
    campaign = _campaign(session, account, contacts, template="متن ثابت")
    rows = _arm(session, campaign, 3)
    for contact in contacts:
        _stage_ready(session, campaign, contact)
    for row in rows[:2]:
        row.send_status = SendStatus.FAILED_RETRYABLE
    campaign.status = CampaignStatus.RUNNING.value
    session.commit()
    claimed = _claim(session, campaign.id, 8)
    assert set(claimed) <= {row.contact_id for row in rows[:3]}
    assert contacts[3].id not in claimed


def test_two_workers_cannot_pass_100_or_double_claim(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="race-claim")
    contacts = [_contact(session, first_name=f"R{i}") for i in range(101)]
    campaign = _campaign(session, account, contacts, template="متن ثابت")
    rows = _arm(session, campaign, 100)
    for contact in contacts:
        _stage_ready(session, campaign, contact)
    campaign.status = CampaignStatus.RUNNING.value
    session.commit()
    campaign_id = int(campaign.id)
    outside_contact_id = int(contacts[100].id)
    cohort_contact_ids = {int(row.contact_id) for row in rows[:100]}
    session.close()
    barrier = Barrier(2)
    found: list[list[int]] = []
    errors: list[BaseException] = []

    def _worker() -> None:
        worker = pg_session_factory()
        try:
            barrier.wait(timeout=30)
            claimed = _claim(worker, campaign_id, 100)
            for item in (
                worker.query(StagedQueueItem)
                .filter(
                    StagedQueueItem.campaign_id == campaign_id,
                    StagedQueueItem.contact_id.in_(claimed or [0]),
                )
                .all()
            ):
                item.status = StagedQueueItemStatus.QUEUED.value
            worker.commit()
            found.append(claimed)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            worker.rollback()
        finally:
            worker.close()

    threads = [Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert thread.is_alive() is False
    assert errors == []
    flat = [contact_id for batch in found for contact_id in batch]
    assert len(flat) == len(set(flat))
    assert len(set(flat)) <= 100
    assert outside_contact_id not in flat
    assert set(flat) <= cohort_contact_ids


def test_connector_rejects_contact_outside_the_cohort(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="connector")
    contacts = [_contact(session, first_name=f"K{i}") for i in range(4)]
    campaign = _campaign(session, account, contacts, template="متن ثابت")
    rows = _arm(session, campaign, 2)
    campaign.status = CampaignStatus.RUNNING.value
    session.commit()
    called = {"n": 0}

    def _send() -> None:
        called["n"] += 1

    decision = admit_connector_call(
        session,
        campaign_id=campaign.id,
        contact_id=rows[3].contact_id,
        kill_switch=False,
        window_open=True,
        connector=_send,
    )
    assert decision == "reject"
    assert called["n"] == 0
    allowed = admit_connector_call(
        session,
        campaign_id=campaign.id,
        contact_id=rows[0].contact_id,
        kill_switch=False,
        window_open=True,
        connector=_send,
    )
    assert allowed == "allow"
    assert called["n"] == 1


def test_reprepare_and_repeat_keep_the_same_cohort(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="keep")
    contacts = [_contact(session, first_name=f"P{i}") for i in range(4)]
    campaign = _campaign(session, account, contacts, template="متن ثابت")
    rows = _arm(session, campaign, 2)
    before = [row.id for row in rows[:2]]
    again = materialize_pilot_cohort(session, campaign.id)
    session.commit()
    assert again["pilot_cohort_size"] == 2
    assert again["pilot_first_recipient_id"] == before[0]
    assert again["pilot_last_recipient_id"] == before[1]
    prepare_campaign_messages(
        session,
        campaign.id,
        PrepareMessagesRequest(force_mock_output=False),
    )
    reprepare_campaign(session, campaign.id, activity_probe=_quiet)
    session.expire_all()
    members = (
        session.query(CampaignPilotCohortMember.campaign_recipient_id)
        .filter_by(campaign_id=campaign.id)
        .order_by(CampaignPilotCohortMember.ordinal.asc())
        .all()
    )
    assert [int(row[0]) for row in members] == before
    extra = _contact(session, first_name="later")
    session.add(
        CampaignRecipient(
            campaign_id=campaign.id,
            contact_id=extra.id,
            render_status=RenderStatus.PENDING,
            send_status=SendStatus.PENDING,
        )
    )
    session.commit()
    materialize_pilot_cohort(session, campaign.id)
    session.commit()
    after = [
        int(row[0])
        for row in session.query(CampaignPilotCohortMember.campaign_recipient_id)
        .filter_by(campaign_id=campaign.id)
        .order_by(CampaignPilotCohortMember.ordinal.asc())
        .all()
    ]
    assert after == before


def test_concurrent_materialize_is_idempotent(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="build-race")
    contacts = [_contact(session, first_name=f"B{i}") for i in range(12)]
    campaign = _campaign(session, account, contacts, template="متن ثابت")
    configure_pilot_recipient_limit(session, campaign.id, 10)
    session.commit()
    campaign_id = int(campaign.id)
    session.close()
    barrier = Barrier(2)
    errors: list[BaseException] = []

    def _worker() -> None:
        worker = pg_session_factory()
        try:
            barrier.wait(timeout=30)
            materialize_pilot_cohort(worker, campaign_id)
            worker.commit()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            worker.rollback()
        finally:
            worker.close()

    threads = [Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert thread.is_alive() is False
    assert errors == []
    check = pg_session_factory()
    ordinals = [
        int(row[0])
        for row in check.query(CampaignPilotCohortMember.ordinal)
        .filter_by(campaign_id=campaign_id)
        .order_by(CampaignPilotCohortMember.ordinal.asc())
        .all()
    ]
    assert ordinals == list(range(1, 11))


def test_cohort_smaller_than_the_limit(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="small")
    contacts = [_contact(session, first_name=f"S{i}") for i in range(2)]
    campaign = _campaign(session, account, contacts, template="متن ثابت")
    rows = _arm(session, campaign, 100)
    view = materialize_pilot_cohort(session, campaign.id)
    assert view["pilot_cohort_size"] == 2
    assert view["pilot_first_recipient_id"] == rows[0].id
    assert view["pilot_last_recipient_id"] == rows[1].id
    assert view["pilot_active"] is True
    assert view["pilot_remaining"] == 2


def test_success_100_pauses_and_success_101_is_rejected(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="cap")
    contacts = [_contact(session, first_name=f"U{i}") for i in range(101)]
    campaign = _campaign(session, account, contacts, template="متن ثابت")
    rows = _arm(session, campaign, 100)
    campaign.status = CampaignStatus.RUNNING.value
    session.commit()
    for row in rows[:100]:
        assert record_definitive_success(
            session,
            campaign_id=campaign.id,
            contact_id=row.contact_id,
            message_id=None,
        )
        confirm_pilot_success(session, campaign.id)
    session.commit()
    session.refresh(campaign)
    assert campaign.status == CampaignStatus.PAUSED.value
    assert success_count(session, campaign.id) == 100
    assert (
        record_definitive_success(
            session,
            campaign_id=campaign.id,
            contact_id=rows[100].contact_id,
            message_id=None,
        )
        is False
    )
    assert success_count(session, campaign.id) == 100


def test_exhausted_cohort_pauses_below_100_successes(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="done")
    contacts = [_contact(session, first_name=f"D{i}") for i in range(3)]
    campaign = _campaign(session, account, contacts, template="متن ثابت")
    rows = _arm(session, campaign, 3)
    campaign.status = CampaignStatus.RUNNING.value
    rows[0].send_status = SendStatus.DELIVERED
    rows[1].send_status = SendStatus.FAILED_PERMANENT
    rows[2].send_status = SendStatus.FAILED_PERMANENT
    record_definitive_success(
        session,
        campaign_id=campaign.id,
        contact_id=rows[0].contact_id,
        message_id=None,
    )
    confirm_pilot_success(session, campaign.id)
    assert pause_if_cohort_exhausted(session, campaign.id) is True
    session.commit()
    session.refresh(campaign)
    assert campaign.status == CampaignStatus.PAUSED.value
    assert success_count(session, campaign.id) == 1


def test_stop_and_closed_window_do_not_create_attempts(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="gates")
    contacts = [_contact(session, first_name="G") for _ in range(1)]
    campaign = _campaign(session, account, contacts, template="متن ثابت")
    rows = _arm(session, campaign, 1)
    campaign.status = CampaignStatus.RUNNING.value
    session.commit()
    assert (
        connector_admission(
            session,
            campaign_id=campaign.id,
            contact_id=rows[0].contact_id,
            kill_switch=True,
            window_open=True,
        )
        == "stop"
    )
    assert (
        connector_admission(
            session,
            campaign_id=campaign.id,
            contact_id=rows[0].contact_id,
            kill_switch=False,
            window_open=False,
        )
        == "defer"
    )
    assert connector_admission(
        session,
        campaign_id="nope",
        contact_id=rows[0].contact_id,
        kill_switch=False,
        window_open=True,
    ) == "reject"
    assert session.query(MessageAttempt).count() == 0


@pytest.mark.asyncio
async def test_emergency_stop_blocks_claim_before_the_query():
    class _Redis:
        def __init__(self, value):
            self.value = value

        async def get(self, _key):
            return self.value

    assert await emergency_stop_allows_claim(_Redis("true")) is False
    assert await emergency_stop_allows_claim(_Redis(None)) is True

    class _Broken:
        async def get(self, _key):
            raise RuntimeError("redis down")

    assert await emergency_stop_allows_claim(_Broken()) is False
    from pathlib import Path

    source = Path(__file__).resolve().parents[2].joinpath(
        "core_engine", "services", "queue_bridge.py"
    ).read_text(encoding="utf-8")
    assert source.index("emergency_stop_allows_claim") < source.index("claimed_items = claim_ready_items")


def test_account_limits_and_terminal_retry_policy_stay():
    from pathlib import Path

    source = Path(__file__).resolve().parents[2].joinpath(
        "core_engine", "services", "account_message_limits.py"
    ).read_text(encoding="utf-8")
    assert "def account_hourly_limit" in source
    assert "def account_daily_limit" in source
    assert set(COHORT_TERMINAL_SEND_STATUSES) == set(_TERMINAL_CAMPAIGN_SEND_STATUSES)
    assert SendStatus.FAILED_RETRYABLE not in COHORT_TERMINAL_SEND_STATUSES


def test_disabled_products_do_not_call_afrakala(pg_session_factory, monkeypatch):
    calls = {"n": 0}

    def _fetch():
        calls["n"] += 1
        raise AssertionError("AfraKala was called")

    monkeypatch.setattr(
        "core_engine.services.phase4_prepare.fetch_current_advertising_products",
        _fetch,
    )
    session = pg_session_factory()
    account = _make_account(session, label="no-product")
    contact = _contact(session, first_name="NP")
    campaign = _campaign(session, account, [contact], template="متن ثابت")
    assert campaign.include_products is False
    prepare_campaign_messages(
        session,
        campaign.id,
        PrepareMessagesRequest(force_mock_output=False),
    )
    assert calls["n"] == 0


def test_reset_requires_a_stopped_idle_campaign(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="reset")
    contacts = [_contact(session, first_name=f"Z{i}") for i in range(2)]
    campaign = _campaign(session, account, contacts, template="متن ثابت")
    _arm(session, campaign, 2)
    campaign.status = CampaignStatus.RUNNING.value
    session.commit()
    from core_engine.services.campaign_pilot_cohort import PilotCohortRejected

    with pytest.raises(PilotCohortRejected) as raised:
        reset_pilot_cohort(session, campaign.id, activity_probe=_quiet)
    assert raised.value.code == "CAMPAIGN_RUNNING"
    campaign.status = CampaignStatus.PREPARED.value
    session.commit()
    view = reset_pilot_cohort(session, campaign.id, activity_probe=_quiet)
    session.commit()
    assert view["pilot_active"] is False
    assert view["pilot_cohort_size"] == 0


def test_pilot_cohort_migration_up_and_down_on_pytest_database(pg_session_factory):
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect, text

    from core_engine.database import Base
    from tests.isolation import assert_pytest_database_name, rewrite_database_url

    session = pg_session_factory()
    source = session.get_bind().engine.url.render_as_string(hide_password=False)
    target_name = "mmp_pytest_pilot_cohort_mig"
    assert_pytest_database_name(target_name)
    admin = create_engine(rewrite_database_url(source, "postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        assert str(conn.execute(text("SELECT current_database()")).scalar()) == "postgres"
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": target_name},
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target_name}"'))
    admin.dispose()

    engine = create_engine(rewrite_database_url(source, target_name))
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS campaign_pilot_cohort_members"))
        conn.execute(text("ALTER TABLE campaign_pilot_states DROP COLUMN IF EXISTS recipient_limit"))

    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "campaign_pilot_cohort_001.py"
    )
    spec = importlib.util.spec_from_file_location("campaign_pilot_cohort_001", path)
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

    def _names() -> set[str]:
        return set(inspect(engine).get_table_names())

    def _constraints() -> set[str]:
        return {
            item["name"]
            for item in inspect(engine).get_unique_constraints("campaign_pilot_cohort_members")
        }

    _run(module.upgrade)
    assert "campaign_pilot_cohort_members" in _names()
    assert {
        "uq_pilot_cohort_campaign_recipient",
        "uq_pilot_cohort_campaign_ordinal",
        "uq_pilot_cohort_campaign_contact",
    } <= _constraints()
    columns = {col["name"] for col in inspect(engine).get_columns("campaign_pilot_states")}
    assert "recipient_limit" in columns
    _run(module.downgrade)
    assert "campaign_pilot_cohort_members" not in _names()
    columns = {col["name"] for col in inspect(engine).get_columns("campaign_pilot_states")}
    assert "recipient_limit" not in columns
    _run(module.upgrade)
    assert "campaign_pilot_cohort_members" in _names()
    engine.dispose()
