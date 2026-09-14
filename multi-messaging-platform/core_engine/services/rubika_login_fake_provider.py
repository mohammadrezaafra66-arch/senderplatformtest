"""Fake Rubika login provider for isolated L3 tests (no live OTP/network)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

from core_engine.services.rubika_login_state_machine import (
    OTP_INVALID,
    OTP_REQUEST_FAILED,
    CandidateProveResult,
    ProviderOtpRequestResult,
    ProviderOtpSubmitResult,
)
from core_engine.services.session_storage import load_channel_session_plaintext
from core_engine.services.rubika_user_session import parse_session_envelope
from core_engine.models import ChannelSession


@dataclass
class FakeRubikaLoginProvider:
    """In-memory OTP provider. Stores expected codes only in process memory — never DB."""

    expected_code: str = "123456"
    guid: str = "fake-guid-001"
    phone_e164: str = "989120000001"
    fail_request: bool = False
    fail_submit: bool = False
    _issued: dict[str, str] = field(default_factory=dict)  # provider_challenge_id -> code

    async def request_otp(self, *, phone_e164: str, account_id: int) -> ProviderOtpRequestResult:
        if self.fail_request:
            return ProviderOtpRequestResult(ok=False, code=OTP_REQUEST_FAILED)
        provider_challenge_id = secrets.token_urlsafe(12)
        # Remember expected code in memory only (not on challenge row).
        self._issued[provider_challenge_id] = self.expected_code
        self.phone_e164 = phone_e164
        return ProviderOtpRequestResult(
            ok=True,
            code="OTP_SENT",
            provider_challenge_id=provider_challenge_id,
            secret_blob={"handshake": "ok"},  # no OTP here
        )

    async def submit_otp(
        self,
        *,
        phone_e164: str,
        provider_challenge_id: str | None,
        secret_blob: dict[str, Any],
        code: str,
    ) -> ProviderOtpSubmitResult:
        if self.fail_submit:
            return ProviderOtpSubmitResult(ok=False, code=OTP_INVALID)
        expected = self._issued.get(str(provider_challenge_id or ""))
        if expected is None or code.strip() != expected:
            return ProviderOtpSubmitResult(ok=False, code=OTP_INVALID)
        return ProviderOtpSubmitResult(
            ok=True,
            code="OK",
            phone_number=phone_e164 or self.phone_e164,
            auth=secrets.token_hex(16),
            guid=self.guid,
            user_agent="fake-ua",
            private_key="-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----",
        )


@dataclass
class PassThroughCandidateProver:
    """Decrypt+structure only — treats persistence as sufficient for isolated tests."""

    force_fail_code: str | None = None
    force_guid: str | None = None

    async def prove(self, db, *, account_id: int, session_id: int, expected_guid: str | None):
        if self.force_fail_code:
            return CandidateProveResult(
                ok=False,
                code=self.force_fail_code,
                identity_guid=expected_guid,
            )
        row = db.query(ChannelSession).filter(ChannelSession.id == int(session_id)).first()
        if row is None:
            return CandidateProveResult(ok=False, code="SESSION_MISSING")
        plaintext = load_channel_session_plaintext(row)
        envelope = parse_session_envelope(plaintext)
        guid = self.force_guid or envelope.get("guid") or expected_guid
        return CandidateProveResult(ok=True, code="PROVE_OK", identity_guid=guid)
