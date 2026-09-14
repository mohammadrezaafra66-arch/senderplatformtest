"""L11 isolated tests — Rubika worker discovery modes & eligibility."""

from __future__ import annotations

import pytest

from core_engine.models import (
    Account,
    AccountStatus,
    ChannelSession,
    PlatformType,
    RubikaAccountPool,
    RubikaSessionStatus,
    SessionType,
)
from workers.multi_account_worker import MultiAccountWorker
from workers.rubika_pool_worker import RubikaPoolWorker
from workers.rubika_worker_discovery import (
    MODE_DYNAMIC,
    MODE_PINNED,
    MODE_SHADOW,
    compare_discovery_sets,
    get_dispatch_eligible_rubika_account_ids,
    reconcile_worker_account_ids,
    resolve_actual_worker_account_ids,
)


@pytest.fixture
def db(pg_session_factory):
    session = pg_session_factory()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def phase_day(monkeypatch):
    monkeypatch.setattr(
        "workers.rubika_account_pool.resolve_current_phase",
        lambda db, clock=None: "day",
    )


def _acct(db, *, status=AccountStatus.ACTIVE, guid: str | None = None) -> Account:
    a = Account(
        platform=PlatformType.RUBIKA,
        status=status,
        phone_number="+989120000001",
        label="l11",
        rubika_guid=guid,
    )
    db.add(a)
    db.flush()
    return a


def _pool(db, aid: int, phase: str = "day") -> None:
    db.add(RubikaAccountPool(account_id=aid, phase=phase, priority=1))
    db.flush()


def _session(
    db,
    aid: int,
    *,
    status: RubikaSessionStatus,
    ciphertext: str = "cipher",
    identity: str | None = "guid-1",
) -> ChannelSession:
    row = ChannelSession(
        account_id=aid,
        session_type=SessionType.RUBIKA_SESSION,
        ciphertext=ciphertext,
        key_version=1,
        session_status=status,
        identity_guid=identity,
    )
    db.add(row)
    db.flush()
    return row


def test_pinned_mode_returns_exactly_configured_ids():
    actual = resolve_actual_worker_account_ids(
        mode=MODE_PINNED,
        pinned_ids=[12, 79],
        dynamic_eligible_ids=[1, 12, 13, 23, 74, 79],
    )
    assert actual == [12, 79]


def test_shadow_mode_does_not_change_actual_worker_set():
    snap = compare_discovery_sets(
        mode=MODE_SHADOW,
        pinned_ids=[12, 79],
        dynamic_eligible_ids=[12, 13, 23, 74, 79],
    )
    assert snap.actual_worker_ids == [12, 79]
    assert snap.would_add == [13, 23, 74]


def test_shadow_mode_no_worker_creation_for_discovered_only():
    actual = resolve_actual_worker_account_ids(
        mode=MODE_SHADOW,
        pinned_ids=[12, 79],
        dynamic_eligible_ids=[13, 23, 74],
    )
    assert 13 not in actual and 23 not in actual and 74 not in actual


def test_dynamic_mode_uses_authoritative_eligibility():
    actual = resolve_actual_worker_account_ids(
        mode=MODE_DYNAMIC,
        pinned_ids=[12, 79],
        dynamic_eligible_ids=[13, 23, 74],
    )
    assert actual == [13, 23, 74]


def test_dynamic_cohort_limits_coverage():
    actual = resolve_actual_worker_account_ids(
        mode=MODE_DYNAMIC,
        pinned_ids=[12, 79],
        dynamic_eligible_ids=[13, 23, 74],
        cohort_ids=[13],
    )
    # Canary preserves pin and adds only cohort∩eligible.
    assert actual == [12, 13, 79]


def test_dynamic_empty_cohort_drops_non_eligible_pin():
    """Final dynamic (empty cohort) uses eligible only — Account12-shaped drop."""
    actual = resolve_actual_worker_account_ids(
        mode=MODE_DYNAMIC,
        pinned_ids=[12, 79],
        dynamic_eligible_ids=[13, 23, 74, 79],
        cohort_ids=[],
    )
    assert actual == [13, 23, 74, 79]
    assert 12 not in actual


def test_no_session_account_excluded(db, phase_day):
    a = _acct(db)
    _pool(db, a.id)
    ids = get_dispatch_eligible_rubika_account_ids(db)
    assert a.id not in ids


def test_decrypt_failed_account_excluded(db, phase_day, monkeypatch):
    a = _acct(db, guid=None)
    _pool(db, a.id)
    _session(
        db,
        a.id,
        status=RubikaSessionStatus.LEGACY_UNCLASSIFIED,
        ciphertext="bad",
        identity=None,
    )

    def _boom(_row):
        raise RuntimeError("decrypt")

    monkeypatch.setattr(
        "core_engine.services.session_storage.load_channel_session_plaintext",
        _boom,
    )
    ids = get_dispatch_eligible_rubika_account_ids(db)
    assert a.id not in ids


def test_disabled_account_excluded(db, phase_day):
    a = _acct(db, status=AccountStatus.RESTING, guid="g")
    _pool(db, a.id)
    _session(db, a.id, status=RubikaSessionStatus.ACTIVE)
    ids = get_dispatch_eligible_rubika_account_ids(db)
    assert a.id not in ids


def test_eligible_canonical_account_included(db, phase_day):
    a = _acct(db, guid="guid-13")
    _pool(db, a.id)
    _session(db, a.id, status=RubikaSessionStatus.ACTIVE, identity="guid-13")
    ids = get_dispatch_eligible_rubika_account_ids(db)
    assert a.id in ids


def test_ambiguous_legacy_excluded_explicit(db, phase_day):
    a = _acct(db, guid="g12")
    _pool(db, a.id)
    _session(db, a.id, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED)
    _session(db, a.id, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED)
    ids = get_dispatch_eligible_rubika_account_ids(db)
    assert a.id not in ids


def test_single_legacy_eligible_when_decrypt_ok(db, phase_day, monkeypatch):
    a = _acct(db, guid="g79")
    _pool(db, a.id)
    _session(
        db,
        a.id,
        status=RubikaSessionStatus.LEGACY_UNCLASSIFIED,
        identity=None,
    )
    monkeypatch.setattr(
        "core_engine.services.session_storage.load_channel_session_plaintext",
        lambda row: b'{"auth":"x"}',
    )
    ids = get_dispatch_eligible_rubika_account_ids(db)
    assert a.id in ids


def test_duplicate_discovery_does_not_duplicate_worker():
    next_ids, added, removed = reconcile_worker_account_ids([12, 79], [79, 12, 12, 79])
    assert next_ids == [12, 79]
    assert added == []
    assert removed == []


def test_stale_coverage_reconciles_safely():
    next_ids, added, removed = reconcile_worker_account_ids([12, 79], [12, 13, 23])
    assert next_ids == [12, 13, 23]
    assert added == [13, 23]
    assert removed == [79]


def test_repeated_reconciliation_idempotent():
    a, _, _ = reconcile_worker_account_ids([13, 23, 74], [13, 23, 74])
    b, added, removed = reconcile_worker_account_ids(a, [13, 23, 74])
    assert a == b == [13, 23, 74]
    assert added == [] and removed == []


def test_account_becoming_eligible_discovered_without_restart(db, phase_day):
    a = _acct(db, guid="g74")
    _pool(db, a.id)
    assert a.id not in get_dispatch_eligible_rubika_account_ids(db)
    _session(db, a.id, status=RubikaSessionStatus.ACTIVE, identity="g74")
    assert a.id in get_dispatch_eligible_rubika_account_ids(db)


def test_account_becoming_ineligible_withdrawn(db, phase_day):
    a = _acct(db, guid="g23")
    _pool(db, a.id)
    _session(db, a.id, status=RubikaSessionStatus.ACTIVE, identity="g23")
    assert a.id in get_dispatch_eligible_rubika_account_ids(db)
    a.status = AccountStatus.REQUIRES_LOGIN
    db.flush()
    assert a.id not in get_dispatch_eligible_rubika_account_ids(db)


def test_discovery_never_selects_session_by_max_id(db, phase_day):
    a = _acct(db, guid="g")
    _pool(db, a.id)
    s1 = _session(db, a.id, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED)
    s2 = _session(db, a.id, status=RubikaSessionStatus.LEGACY_UNCLASSIFIED)
    assert s2.id > s1.id
    assert a.id not in get_dispatch_eligible_rubika_account_ids(db)


def test_pinned_12_79_behavior_unchanged():
    assert resolve_actual_worker_account_ids(
        mode=MODE_PINNED, pinned_ids=[12, 79], dynamic_eligible_ids=[1, 2, 13]
    ) == [12, 79]


def test_batch_a_shaped_fixtures(db, phase_day):
    ids_created = []
    for _ in range(3):
        a = _acct(db, guid="g")
        a.rubika_guid = f"g{a.id}"
        _pool(db, a.id)
        _session(db, a.id, status=RubikaSessionStatus.ACTIVE, identity=f"g{a.id}")
        ids_created.append(a.id)
    ids = get_dispatch_eligible_rubika_account_ids(db)
    assert set(ids_created).issubset(set(ids))
    snap = compare_discovery_sets(
        mode=MODE_SHADOW,
        pinned_ids=[12, 79],
        dynamic_eligible_ids=ids_created + [12, 79],
    )
    assert snap.actual_worker_ids == [12, 79]


def test_shadow_discovery_makes_no_db_mutation(db, phase_day):
    before = db.query(Account).count()
    snap = compare_discovery_sets(
        mode=MODE_SHADOW, pinned_ids=[12, 79], dynamic_eligible_ids=[13, 23, 74]
    )
    assert snap.mutated_db is False
    assert db.query(Account).count() == before


def test_shadow_discovery_makes_no_redis_mutation():
    snap = compare_discovery_sets(
        mode=MODE_SHADOW, pinned_ids=[12, 79], dynamic_eligible_ids=[13]
    )
    assert snap.mutated_redis is False


def test_no_send_otp_path_from_discovery_module():
    import workers.rubika_worker_discovery as mod

    src = open(mod.__file__, encoding="utf-8").read()
    assert "request_rubika_login" not in src
    assert "deliver_platform_message" not in src
    assert "request_otp" not in src.lower()


def test_replace_account_ids_idempotent_no_dupes():
    class Stub(RubikaPoolWorker):
        def __init__(self):
            MultiAccountWorker.__init__(
                self,
                platform="rubika",
                account_ids=[12, 79],
                redis_url="redis://test",
                database_url="postgresql://test",
                poll_interval_seconds=1,
                execution_enabled=True,
            )
            self._settings = None
            self._account_refresh_interval_seconds = 0
            self._heartbeat_interval_seconds = 15
            self._heartbeat_ttl_seconds = 45
            self._hostname = "t"
            self._discovery_mode = MODE_PINNED
            self._pinned_account_ids = [12, 79]
            self._explicit_account_ids = [12, 79]

    w = Stub()
    w.replace_account_ids([79, 12, 12])
    assert w.account_ids == [12, 79]
    w.replace_account_ids([12, 79])
    assert w.account_ids == [12, 79]
