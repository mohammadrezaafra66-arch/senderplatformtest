"""L4 — Real Rubika candidate prover (isolated DB, stub network, no live OTP)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountStatus,
    PlatformType,
    RubikaIdentityStatus,
    RubikaSessionStatus,
    SessionType,
)
from core_engine.services.rubika_candidate_prover import (
    AUTH_RECONNECT_FAILED,
    AUTH_RECONNECT_TIMEOUT,
    IDENTITY_MISMATCH,
    IDENTITY_MISSING,
    PROOF_PASS,
    RealRubikaCandidateProver,
    SESSION_DECRYPT_FAILED,
    SESSION_STRUCTURALLY_INVALID,
    assert_prover_allowed_for_canonical,
    resolve_canonical_candidate_prover,
    _FORBIDDEN_CLIENT_METHODS,
)
from core_engine.services.rubika_login_fake_provider import PassThroughCandidateProver
from core_engine.models import ChannelSession
from core_engine.services.rubika_identity import bind_or_verify_rubika_identity
from core_engine.services.rubika_session_promotion import promote_validated_session
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_V1", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _acct(db, *, guid: str | None = None, status=RubikaIdentityStatus.UNBOUND):
    a = Account(
        platform=PlatformType.RUBIKA,
        phone_number="989120000222",
        label="l4",
        status=AccountStatus.ACTIVE,
        rubika_guid=guid,
        rubika_identity_status=status,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _candidate(db, account_id: int, *, guid: str = "guid-candidate", corrupt: str | None = None):
    env = build_session_envelope(
        phone_number="989120000222",
        auth="auth-token",
        guid=guid,
        user_agent="ua",
        private_key="-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----",
    )
    if corrupt == "json":
        env = "{not-json"
    row = store_channel_session(
        db,
        account_id=account_id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=env if corrupt != "json" else env,
        session_status=RubikaSessionStatus.VALIDATING,
        identity_guid=guid,
        login_attempt_id="ch-l4",
    )
    db.commit()
    return row


@dataclass
class StubClient:
    guid: str = "guid-candidate"
    connect_ok: bool = True
    identity_guid: str | None = "guid-candidate"
    connect_delay: float = 0
    fail_connect: bool = False
    calls: list[str] = field(default_factory=list)

    async def connect(self):
        self.calls.append("connect")
        if self.connect_delay:
            await asyncio.sleep(self.connect_delay)
        if self.fail_connect:
            raise RuntimeError("connect failed")

    async def disconnect(self):
        self.calls.append("disconnect")

    async def get_me(self):
        self.calls.append("get_me")
        if self.identity_guid is None:
            return {}
        return {"user_guid": self.identity_guid}

    async def send_message(self, *a, **k):
        self.calls.append("send_message")
        raise AssertionError("forbidden")

    async def send_code(self, *a, **k):
        self.calls.append("send_code")
        raise AssertionError("forbidden")


@dataclass
class StubNetwork:
    client: StubClient = field(default_factory=StubClient)
    build_calls: int = 0

    async def build_client(self, envelope: dict[str, str]):
        self.build_calls += 1
        self.client.guid = envelope.get("guid", self.client.guid)
        return self.client

    async def connect(self, client: StubClient):
        await client.connect()

    async def probe_identity(self, client: StubClient):
        await client.get_me()
        return "get_me", client.identity_guid

    async def disconnect(self, client: StubClient):
        await client.disconnect()


@pytest.mark.asyncio
async def test_valid_candidate_proof_pass(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    row = _candidate(db, acct.id)
    net = StubNetwork()
    prover = RealRubikaCandidateProver(network=net)
    result = await prover.prove_detailed(db, account_id=acct.id, session_id=row.id, expected_guid="guid-candidate")
    assert result.proof_status == PROOF_PASS
    assert result.returned_identity_present
    assert result.identity_match is True
    assert "token" not in result.sanitized_message.lower()


@pytest.mark.asyncio
async def test_reconnect_failure_blocks_active(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    row = _candidate(db, acct.id)
    client = StubClient(fail_connect=True)
    net = StubNetwork(client=client)
    prover = RealRubikaCandidateProver(network=net)
    result = await prover.prove_detailed(db, account_id=acct.id, session_id=row.id, expected_guid="guid-candidate")
    assert result.proof_status == AUTH_RECONNECT_FAILED
    row.session_status = RubikaSessionStatus.INVALID
    db.commit()
    promo = promote_validated_session(db, account_id=acct.id, candidate_session_id=row.id)
    assert not promo.ok


@pytest.mark.asyncio
async def test_timeout_blocks_active(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    row = _candidate(db, acct.id)
    client = StubClient(connect_delay=0.2)
    net = StubNetwork(client=client)
    prover = RealRubikaCandidateProver(network=net, timeout_seconds=0.05)
    result = await prover.prove_detailed(db, account_id=acct.id, session_id=row.id, expected_guid="guid-candidate")
    assert result.proof_status == AUTH_RECONNECT_TIMEOUT


@pytest.mark.asyncio
async def test_structural_invalid_blocks(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    row = _candidate(db, acct.id, corrupt="json")
    prover = RealRubikaCandidateProver(network=StubNetwork())
    result = await prover.prove_detailed(db, account_id=acct.id, session_id=row.id, expected_guid="x")
    assert result.proof_status == SESSION_STRUCTURALLY_INVALID


@pytest.mark.asyncio
async def test_identity_mismatch_blocks(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db, guid="bound-other", status=RubikaIdentityStatus.VERIFIED)
    row = _candidate(db, acct.id, guid="guid-candidate")
    prover = RealRubikaCandidateProver(network=StubNetwork())
    result = await prover.prove_detailed(db, account_id=acct.id, session_id=row.id, expected_guid="guid-candidate")
    assert result.proof_status == IDENTITY_MISMATCH


@pytest.mark.asyncio
async def test_unbound_first_time_identity_ok(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    row = _candidate(db, acct.id)
    prover = RealRubikaCandidateProver(network=StubNetwork())
    result = await prover.prove_detailed(db, account_id=acct.id, session_id=row.id, expected_guid="guid-candidate")
    assert result.ok
    bind = bind_or_verify_rubika_identity(acct, result.identity_guid)
    assert bind.ok and bind.newly_bound


@pytest.mark.asyncio
async def test_existing_same_identity_succeeds(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db, guid="guid-candidate", status=RubikaIdentityStatus.VERIFIED)
    row = _candidate(db, acct.id)
    prover = RealRubikaCandidateProver(network=StubNetwork())
    result = await prover.prove_detailed(db, account_id=acct.id, session_id=row.id, expected_guid="guid-candidate")
    assert result.ok


@pytest.mark.asyncio
async def test_existing_different_identity_fails(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db, guid="guid-old", status=RubikaIdentityStatus.VERIFIED)
    row = _candidate(db, acct.id, guid="guid-new")
    prover = RealRubikaCandidateProver(network=StubNetwork())
    result = await prover.prove_detailed(db, account_id=acct.id, session_id=row.id, expected_guid="guid-new")
    assert result.proof_status == IDENTITY_MISMATCH


@pytest.mark.asyncio
async def test_disconnect_on_success(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    row = _candidate(db, acct.id)
    client = StubClient()
    net = StubNetwork(client=client)
    prover = RealRubikaCandidateProver(network=net)
    await prover.prove_detailed(db, account_id=acct.id, session_id=row.id, expected_guid="guid-candidate")
    assert "disconnect" in client.calls


@pytest.mark.asyncio
async def test_disconnect_on_failure(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    row = _candidate(db, acct.id)
    client = StubClient(fail_connect=True)
    net = StubNetwork(client=client)
    prover = RealRubikaCandidateProver(network=net)
    await prover.prove_detailed(db, account_id=acct.id, session_id=row.id, expected_guid="guid-candidate")
    assert "disconnect" in client.calls


@pytest.mark.asyncio
async def test_no_send_method_invoked(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    row = _candidate(db, acct.id)
    client = StubClient()
    net = StubNetwork(client=client)
    prover = RealRubikaCandidateProver(network=net)
    await prover.prove_detailed(db, account_id=acct.id, session_id=row.id, expected_guid="guid-candidate")
    forbidden = set(client.calls) & _FORBIDDEN_CLIENT_METHODS
    assert not forbidden


@pytest.mark.asyncio
async def test_no_otp_request_invoked(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    row = _candidate(db, acct.id)
    with patch(
        "core_engine.services.rubika_candidate_prover._build_client_from_envelope",
        side_effect=AssertionError("otp path"),
    ):
        prover = RealRubikaCandidateProver(network=StubNetwork())
        result = await prover.prove_detailed(db, account_id=acct.id, session_id=row.id, expected_guid="guid-candidate")
    assert result.ok


@pytest.mark.asyncio
async def test_no_extra_session_persistence(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    row = _candidate(db, acct.id)
    before = db.query(ChannelSession).count()
    prover = RealRubikaCandidateProver(network=StubNetwork())
    await prover.prove_detailed(db, account_id=acct.id, session_id=row.id, expected_guid="guid-candidate")
    after = db.query(ChannelSession).count()
    assert before == after


def test_passthrough_forbidden_outside_tests(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_V1", "true")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="PassThroughCandidateProver"):
        assert_prover_allowed_for_canonical(PassThroughCandidateProver())
    get_settings.cache_clear()


def test_passthrough_allowed_under_pytest():
    assert_prover_allowed_for_canonical(PassThroughCandidateProver())


def test_resolve_returns_real_prover():
    prover = resolve_canonical_candidate_prover()
    assert isinstance(prover, RealRubikaCandidateProver)


@pytest.mark.asyncio
async def test_decrypt_failure(pg_session_factory, monkeypatch):
    db = pg_session_factory()
    acct = _acct(db)
    row = _candidate(db, acct.id)
    from core_engine.services.crypto import SessionDecryptionError
    import core_engine.services.rubika_candidate_prover as prover_mod

    def _boom(*a, **k):
        raise SessionDecryptionError("bad key")

    monkeypatch.setattr(prover_mod, "load_channel_session_plaintext", _boom)
    prover = RealRubikaCandidateProver(network=StubNetwork())
    result = await prover.prove_detailed(db, account_id=acct.id, session_id=row.id, expected_guid="guid-candidate")
    assert result.proof_status == SESSION_DECRYPT_FAILED


@pytest.mark.asyncio
async def test_identity_missing(pg_session_factory):
    db = pg_session_factory()
    acct = _acct(db)
    row = _candidate(db, acct.id)
    client = StubClient(identity_guid=None)
    net = StubNetwork(client=client)
    prover = RealRubikaCandidateProver(network=net)
    result = await prover.prove_detailed(db, account_id=acct.id, session_id=row.id, expected_guid="guid-candidate")
    assert result.proof_status == IDENTITY_MISSING
