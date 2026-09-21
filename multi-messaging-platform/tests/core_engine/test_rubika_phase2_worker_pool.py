"""Phase 2 — Rubika pool enrollment, discovery, heartbeat, queue binding.

Hermetic only. No live OTP, send, worker, or celery beat.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountStatus,
    ChannelSession,
    PlatformType,
    RubikaAccountPool,
    RubikaIdentityStatus,
    RubikaSessionStatus,
    SessionType,
)
from core_engine.services.account_runtime_status import (
    AUTHENTICATED_LABEL_FA,
    WORKER_READY_LABEL_FA,
    WORKER_STALE_LABEL_FA,
    RuntimeStatus,
    compute_account_runtime_status,
    coverage_liveness,
)
from core_engine.services.campaign_sender_eligibility import evaluate_campaign_sender_eligibility
from core_engine.services.queue_bridge import (
    campaign_accepts_new_dispatch,
    payload_sender_assignment_is_stable,
    push_staged_items_to_worker_queue,
)
from core_engine.services.rubika_account_lifecycle import (
    SESSION_INVALIDATED,
    apply_rubika_session_invalidation,
    resolve_create_status,
)
from core_engine.services.rubika_l17_automation import (
    POOL_ENROLLMENT_FAILED,
    POOL_ENROLLMENT_OK,
    ensure_rubika_pool_membership,
)
from core_engine.services.rubika_login_fake_provider import (
    FakeRubikaLoginProvider,
    PassThroughCandidateProver,
)
from core_engine.services.rubika_login_state_machine import (
    request_rubika_login,
    submit_rubika_login_code,
)
from workers.config import WorkerSettings
from workers.multi_account_worker import MultiAccountWorker
from workers.payloads import WorkerPayload
from workers.pool_health import WORKER_COVERAGE_LAST_SEEN_TTL_SECONDS, publish_account_coverage
from workers.redis_keys import worker_account_coverage_key, worker_account_coverage_last_key
from workers.rubika_account_pool import RubikaAccountPoolManager
from workers.rubika_worker_discovery import (
    MODE_DYNAMIC,
    MODE_PINNED,
    get_dispatch_eligible_rubika_account_ids,
    resolve_actual_worker_account_ids,
)
from workers.worker_runtime import validate_worker_payload


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", Fernet.generate_key().decode())
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_V1", "true")
    monkeypatch.setenv("RUBIKA_DELIVERY_MODE", "user_account")
    monkeypatch.setenv("RUBIKA_USER_ACCOUNT_ENABLED", "true")
    monkeypatch.setenv("AUTO_ENROLL_RUBIKA_POOL", "false")
    monkeypatch.setenv("REAL_QUEUE_PUSH_ENABLED", "false")
    monkeypatch.setenv("REAL_MESSAGE_SENDING_ENABLED", "false")
    monkeypatch.setenv("WORKER_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("RUBIKA_MANAGER_ACTIVATION_REQUIRED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def canonical_ok(monkeypatch):
    monkeypatch.setattr(
        "core_engine.services.rubika_canonical_session.load_canonical_rubika_session",
        lambda *args, **kwargs: object(),
    )


@pytest.fixture
def db(pg_session_factory):
    session = pg_session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def phase_day(monkeypatch):
    monkeypatch.setattr(
        "workers.rubika_account_pool.resolve_current_phase",
        lambda _db, clock=None: "day",
    )


def _acct(db, *, phone, status=AccountStatus.ACTIVE, guid=None, platform=PlatformType.RUBIKA):
    account = Account(
        platform=platform,
        phone_number=phone,
        label="p2",
        status=status,
        rubika_guid=guid,
        rubika_identity_status=(
            RubikaIdentityStatus.VERIFIED if guid else RubikaIdentityStatus.UNBOUND
        ),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def _session(db, account, *, status=RubikaSessionStatus.ACTIVE, guid="guid-1"):
    row = ChannelSession(
        account_id=account.id,
        session_type=SessionType.RUBIKA_SESSION,
        ciphertext="cipher",
        key_version=1,
        session_status=status,
        identity_guid=guid,
        file_path="session.bin",
    )
    db.add(row)
    db.commit()
    return row


def _pool(db, account_id, phase="day", last_error=None):
    row = RubikaAccountPool(
        account_id=account_id,
        phase=phase,
        priority=100,
        last_error_message=last_error,
    )
    db.add(row)
    db.commit()
    return row


async def _login(db, account):
    store: dict = {}
    provider = FakeRubikaLoginProvider()
    requested = await request_rubika_login(
        db, account.id, phone_number=account.phone_number, provider=provider, secret_store=store
    )
    db.commit()
    submitted = await submit_rubika_login_code(
        db,
        account.id,
        requested.challenge_id,
        "123456",
        provider=provider,
        prover=PassThroughCandidateProver(),
        secret_store=store,
    )
    db.commit()
    return submitted


@pytest.mark.asyncio
async def test_successful_login_auto_enrolls_once(db):
    account = _acct(db, phone="989199920001", status=AccountStatus.REQUIRES_LOGIN)
    result = await _login(db, account)
    assert result.ok is True
    rows = db.query(RubikaAccountPool).filter_by(account_id=account.id).all()
    assert len(rows) == 1
    assert rows[0].phase


@pytest.mark.asyncio
async def test_repeated_login_is_idempotent(db):
    account = _acct(db, phone="989199920002", status=AccountStatus.REQUIRES_LOGIN)
    await _login(db, account)
    await _login(db, account)
    assert db.query(RubikaAccountPool).filter_by(account_id=account.id).count() == 1


def test_pool_without_heartbeat_is_authenticated_no_worker(db, phase_day, canonical_ok):
    account = _acct(db, phone="989199920003", guid="g3")
    _session(db, account, guid="g3")
    _pool(db, account.id)
    runtime = compute_account_runtime_status(
        db, account, worker_covered=False, dispatch_eligible_ids={account.id}
    )
    assert runtime.runtime_status == RuntimeStatus.AUTHENTICATED_NO_WORKER.value
    assert runtime.worker_covered is False
    assert runtime.dispatch_ready is False
    assert runtime.runtime_status_label == "احراز شده، Worker آماده نیست"
    elig = evaluate_campaign_sender_eligibility(db, account, runtime=runtime)
    assert elig.campaign_eligible is False


def test_fresh_heartbeat_makes_worker_ready_and_can_be_ready(db, phase_day, canonical_ok):
    account = _acct(db, phone="989199920004", guid="g4")
    _session(db, account, guid="g4")
    _pool(db, account.id)
    runtime = compute_account_runtime_status(
        db,
        account,
        worker_covered=True,
        worker_stale=False,
        dispatch_eligible_ids={account.id},
    )
    assert runtime.worker_covered is True
    assert runtime.worker_heartbeat_fresh is True
    assert runtime.runtime_status == RuntimeStatus.READY.value
    assert runtime.dispatch_ready is True
    elig = evaluate_campaign_sender_eligibility(db, account, runtime=runtime)
    assert elig.campaign_eligible is True


def test_expired_heartbeat_is_not_worker_ready(db, phase_day, canonical_ok):
    account = _acct(db, phone="989199920005", guid="g5")
    _session(db, account, guid="g5")
    _pool(db, account.id)
    runtime = compute_account_runtime_status(
        db,
        account,
        worker_covered=False,
        worker_stale=True,
        dispatch_eligible_ids={account.id},
    )
    assert runtime.runtime_status == RuntimeStatus.AUTHENTICATED_NO_WORKER.value
    assert runtime.worker_covered is False
    assert runtime.worker_heartbeat_fresh is False
    assert runtime.reason_code == "WORKER_STALE"
    assert runtime.runtime_status_label == WORKER_STALE_LABEL_FA
    elig = evaluate_campaign_sender_eligibility(db, account, runtime=runtime)
    assert elig.campaign_eligible is False
    assert elig.runtime_status_label == WORKER_STALE_LABEL_FA


def test_account_a_heartbeat_does_not_ready_account_b(db, phase_day, canonical_ok):
    a = _acct(db, phone="989199920012", guid="g12")
    b = _acct(db, phone="989199920013", guid="g13")
    _session(db, a, guid="g12")
    _session(db, b, guid="g13")
    _pool(db, a.id)
    _pool(db, b.id)
    ready_a = compute_account_runtime_status(
        db, a, worker_covered=True, dispatch_eligible_ids={a.id, b.id}
    )
    not_b = compute_account_runtime_status(
        db, b, worker_covered=False, dispatch_eligible_ids={a.id, b.id}
    )
    assert ready_a.runtime_status == RuntimeStatus.READY.value
    assert not_b.runtime_status == RuntimeStatus.AUTHENTICATED_NO_WORKER.value
    assert worker_account_coverage_key("rubika", a.id) != worker_account_coverage_key("rubika", b.id)


def test_invalid_session_overrides_pool_membership(db, phase_day):
    account = _acct(db, phone="989199920006", guid="g6", status=AccountStatus.ACTIVE)
    _session(db, account, status=RubikaSessionStatus.INVALID, guid="g6")
    _pool(db, account.id, last_error=SESSION_INVALIDATED)
    runtime = compute_account_runtime_status(
        db, account, worker_covered=True, dispatch_eligible_ids={account.id}
    )
    assert runtime.runtime_status != RuntimeStatus.READY.value
    assert runtime.dispatch_ready is False
    eligible = get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])
    assert account.id not in eligible


def test_banned_resting_quarantined_never_ready(db, phase_day, canonical_ok, monkeypatch):
    banned = _acct(db, phone="989199920007", guid="g7", status=AccountStatus.BANNED)
    resting = _acct(db, phone="989199920008", guid="g8", status=AccountStatus.RESTING)
    quarantined = _acct(db, phone="989199920009", guid="g9")
    for account, guid in ((banned, "g7"), (resting, "g8"), (quarantined, "g9")):
        _session(db, account, guid=guid)
        _pool(db, account.id)
    banned_rt = compute_account_runtime_status(
        db, banned, worker_covered=True, dispatch_eligible_ids={banned.id}
    )
    resting_rt = compute_account_runtime_status(
        db, resting, worker_covered=True, dispatch_eligible_ids={resting.id}
    )
    monkeypatch.setattr(
        "core_engine.services.account_runtime_status.rubika_account_quarantined",
        lambda _account_id: True,
    )
    q_rt = compute_account_runtime_status(
        db, quarantined, worker_covered=True, dispatch_eligible_ids={quarantined.id}
    )
    assert banned_rt.runtime_status != RuntimeStatus.READY.value
    assert resting_rt.runtime_status != RuntimeStatus.READY.value
    assert q_rt.runtime_status != RuntimeStatus.READY.value
    assert q_rt.runtime_status_label == "قرنطینه"
    assert banned_rt.runtime_status_label == "مسدود"
    assert resting_rt.runtime_status_label == "متوقف"


def test_dynamic_discovery_finds_eligible_and_excludes_invalid(db, phase_day):
    ok1 = _acct(db, phone="989199920021", guid="ga")
    ok2 = _acct(db, phone="989199920022", guid="gb")
    bad = _acct(db, phone="989199920023", guid="gc")
    _session(db, ok1, guid="ga")
    _session(db, ok2, guid="gb")
    _session(db, bad, status=RubikaSessionStatus.INVALID, guid="gc")
    _pool(db, ok1.id)
    _pool(db, ok2.id)
    _pool(db, bad.id)
    eligible = get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])
    assert ok1.id in eligible
    assert ok2.id in eligible
    assert bad.id not in eligible
    actual = resolve_actual_worker_account_ids(
        mode=MODE_DYNAMIC,
        pinned_ids=[999001],
        dynamic_eligible_ids=eligible,
    )
    assert 999001 not in actual
    assert ok1.id in actual
    assert ok2.id in actual
    assert bad.id not in actual


def test_pinned_mode_is_explicit_legacy_and_does_not_invent_account_12():
    assert WorkerSettings.model_fields["RUBIKA_WORKER_DISCOVERY_MODE"].default == MODE_DYNAMIC
    assert (
        resolve_actual_worker_account_ids(
            mode=MODE_PINNED,
            pinned_ids=[],
            dynamic_eligible_ids=[12, 13],
        )
        == []
    )
    assert (
        resolve_actual_worker_account_ids(
            mode=MODE_DYNAMIC,
            pinned_ids=[12],
            dynamic_eligible_ids=[13],
        )
        == [13]
    )


def test_restore_does_not_activate_invalid_session(db):
    account = _acct(db, phone="989199920030", status=AccountStatus.REQUIRES_LOGIN, guid="g30")
    _session(db, account, status=RubikaSessionStatus.INVALID, guid="g30")
    RubikaAccountPoolManager(db).mark_account_restored(account_id=account.id)
    db.refresh(account)
    assert account.status == AccountStatus.REQUIRES_LOGIN


def test_session_invalidation_stamps_pool_not_dispatchable(db, phase_day):
    account = _acct(db, phone="989199920031", guid="g31")
    _session(db, account, guid="g31")
    _pool(db, account.id)
    apply_rubika_session_invalidation(db, account, reason=SESSION_INVALIDATED)
    db.commit()
    row = db.query(RubikaAccountPool).filter_by(account_id=account.id).one()
    assert row.last_error_message == SESSION_INVALIDATED
    assert account.id not in get_dispatch_eligible_rubika_account_ids(db, cohort_ids=[])
    runtime = compute_account_runtime_status(db, account, worker_covered=True)
    assert runtime.runtime_status != RuntimeStatus.READY.value


def test_non_rubika_enrollment_rejected(db):
    account = _acct(
        db, phone="989199920040", platform=PlatformType.TELEGRAM, status=AccountStatus.ACTIVE
    )
    result = ensure_rubika_pool_membership(db, account.id)
    assert result.ok is False
    assert result.code == POOL_ENROLLMENT_FAILED
    assert resolve_create_status(PlatformType.TELEGRAM, AccountStatus.ACTIVE) == AccountStatus.ACTIVE


def test_heartbeat_contract_is_account_bound():
    assert coverage_liveness(fresh=True, last_seen=True) == "fresh"
    assert coverage_liveness(fresh=False, last_seen=True) == "stale"
    assert coverage_liveness(fresh=False, last_seen=False) == "missing"
    assert worker_account_coverage_last_key("rubika", 12).endswith(":12")
    assert "13" not in worker_account_coverage_key("rubika", 12)

    class _Redis:
        def __init__(self):
            self.sets = []

        async def set(self, key, value, ex=None):
            self.sets.append((key, value, ex))

    redis = _Redis()
    asyncio.run(
        publish_account_coverage(
            redis,
            platform="rubika",
            account_ids=[12],
            hostname="worker-12",
            ttl_seconds=45,
        )
    )
    keys = [item[0] for item in redis.sets]
    assert worker_account_coverage_key("rubika", 12) in keys
    assert worker_account_coverage_key("rubika", 13) not in keys
    payload = json.loads(redis.sets[0][1])
    assert payload["account_id"] == 12
    assert payload["hostname"] == "worker-12"
    last = [item for item in redis.sets if item[0] == worker_account_coverage_last_key("rubika", 12)]
    assert last and last[0][2] == WORKER_COVERAGE_LAST_SEEN_TTL_SECONDS


class _ProbeWorker(MultiAccountWorker):
    async def send_message(self, payload):
        raise AssertionError("send must not run")


class _FakeRedis:
    def __init__(self, queues):
        self.queues = queues
        self.lpopped = []

    async def lpop(self, key):
        self.lpopped.append(key)
        items = self.queues.get(key) or []
        if not items:
            return None
        return items.pop(0)

    async def get(self, _key):
        return None

    async def exists(self, _key):
        return 0


@pytest.mark.asyncio
async def test_worker_cannot_consume_another_account_queue():
    redis = _FakeRedis({"queue:rubika:12": ['{"account_id":"12"}']})
    worker = _ProbeWorker(
        platform="rubika",
        account_ids=[13],
        redis_url="redis://127.0.0.1:1",
        database_url="postgresql://unused",
        execution_enabled=False,
    )
    worker._redis = redis
    assert await worker.read_next_payload() is None
    assert "queue:rubika:12" not in redis.lpopped
    assert redis.lpopped == ["queue:rubika:13"]


def test_queue_payload_account_id_stays_stable():
    payload = {
        "message_id": 1,
        "campaign_id": 9,
        "contact_id": 3,
        "account_id": 12,
        "platform": "rubika",
        "recipient": "98912",
        "recipient_type": "phone",
        "message_text": "hi",
        "dedupe_key": "d",
    }
    parsed = validate_worker_payload(
        json.dumps(payload),
        platform="rubika",
        allowed_account_ids={"12"},
        logger=logging.getLogger("phase2"),
    )
    assert int(parsed.account_id) == 12
    with pytest.raises(Exception):
        validate_worker_payload(
            json.dumps(payload),
            platform="rubika",
            allowed_account_ids={"13"},
            logger=logging.getLogger("phase2"),
        )
    assert payload_sender_assignment_is_stable(
        message_account_id=12,
        payload_account_id=12,
        message_campaign_id=9,
        item_campaign_id=9,
        message_contact_id=3,
        item_contact_id=3,
    )
    assert not payload_sender_assignment_is_stable(
        message_account_id=12,
        payload_account_id=13,
        message_campaign_id=9,
        item_campaign_id=9,
        message_contact_id=3,
        item_contact_id=3,
    )
    assert WorkerPayload.model_validate(payload).account_id == 12


@pytest.mark.asyncio
async def test_periodic_push_is_idempotent_when_queue_push_disabled(db):
    first = await push_staged_items_to_worker_queue(db)
    second = await push_staged_items_to_worker_queue(db)
    assert first["pushed"] == 0
    assert second["pushed"] == 0
    assert first == second


def test_paused_and_stopped_campaigns_do_not_dispatch():
    assert campaign_accepts_new_dispatch("running") is True
    assert campaign_accepts_new_dispatch("paused") is False
    assert campaign_accepts_new_dispatch("cancelled") is False
    assert campaign_accepts_new_dispatch("completed") is False
    assert campaign_accepts_new_dispatch("draft") is False
    assert campaign_accepts_new_dispatch("stopped") is False


def test_direct_enroll_is_idempotent_and_does_not_mark_ready(db, canonical_ok):
    account = _acct(db, phone="989199920050", guid="g50")
    _session(db, account, guid="g50")
    first = ensure_rubika_pool_membership(db, account.id)
    second = ensure_rubika_pool_membership(db, account.id)
    assert first.code == POOL_ENROLLMENT_OK
    assert first.created is True
    assert second.created is False
    assert db.query(RubikaAccountPool).filter_by(account_id=account.id).count() == 1
    runtime = compute_account_runtime_status(db, account, worker_covered=False)
    assert runtime.runtime_status == RuntimeStatus.AUTHENTICATED_NO_WORKER.value
    assert runtime.details.get("auth_label") == AUTHENTICATED_LABEL_FA


def test_covered_without_dispatch_is_worker_ready_label(db, phase_day, canonical_ok):
    account = _acct(db, phone="989199920051", guid="g51")
    _session(db, account, guid="g51")
    runtime = compute_account_runtime_status(
        db, account, worker_covered=True, dispatch_eligible_ids=set()
    )
    assert runtime.worker_covered is True
    assert runtime.worker_heartbeat_fresh is True
    assert runtime.runtime_status != RuntimeStatus.READY.value
    assert runtime.runtime_status_label == WORKER_READY_LABEL_FA
