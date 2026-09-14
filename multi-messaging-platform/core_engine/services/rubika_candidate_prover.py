"""Real Rubika candidate-session prover (L4).

Proves a VALIDATING candidate session without sending messages, requesting OTP,
or mutating session lifecycle outside the login state machine.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import Account, ChannelSession, RubikaIdentityStatus
from core_engine.services.crypto import SessionDecryptionError
from core_engine.services.rubika_user_session import parse_session_envelope
from core_engine.services.session_storage import load_channel_session_plaintext

logger = logging.getLogger("core_engine.services.rubika_candidate_prover")

PROOF_PASS = "PROOF_PASS"
SESSION_DECRYPT_FAILED = "SESSION_DECRYPT_FAILED"
SESSION_STRUCTURALLY_INVALID = "SESSION_STRUCTURALLY_INVALID"
AUTH_RECONNECT_FAILED = "AUTH_RECONNECT_FAILED"
AUTH_RECONNECT_TIMEOUT = "AUTH_RECONNECT_TIMEOUT"
IDENTITY_MISSING = "IDENTITY_MISSING"
IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
PROVIDER_ERROR = "PROVIDER_ERROR"
SESSION_MISSING = "SESSION_MISSING"

SESSION_STRUCTURE_INVALID = SESSION_STRUCTURALLY_INVALID

_FORBIDDEN_CLIENT_METHODS = frozenset(
    {
        "send_message",
        "send_code",
        "sign_in",
        "add_address_book",
        "join_group",
        "leave_group",
        "logout",
        "logout_all",
    }
)


def _sanitize_message(text: str | None, *, max_len: int = 120) -> str:
    if not text:
        return ""
    msg = str(text)
    msg = re.sub(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+", "<redacted_url>", msg)
    msg = re.sub(r"(?i)\b[a-f0-9]{24,}\b", "<redacted_hex>", msg)
    msg = re.sub(r"(?i)\b[A-Za-z0-9_-]{40,}\b", "<redacted_token>", msg)
    msg = re.sub(r"\+?\d[\d\s\-()]{7,}\d", "<redacted_phone>", msg)
    msg = re.sub(
        r"(?i)\b(auth|token|password|passwd|secret|private_key|api_key|cookie|session)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        msg,
    )
    return re.sub(r"\s+", " ", msg).strip()[:max_len]


def _extract_guid(obj: Any) -> str | None:
    if obj is None:
        return None
    if isinstance(obj, dict):
        for key in ("user_guid", "guid"):
            val = str(obj.get(key) or "").strip()
            if val:
                return val
        user = obj.get("user")
        if isinstance(user, dict):
            return _extract_guid(user)
        return None
    for attr in ("user_guid", "guid"):
        val = str(getattr(obj, attr, None) or "").strip()
        if val:
            return val
    to_dict = getattr(obj, "to_dict", None)
    data = to_dict if isinstance(to_dict, dict) else (to_dict() if callable(to_dict) else None)
    if isinstance(data, dict):
        return _extract_guid(data)
    user = getattr(obj, "user", None)
    if user is not None:
        return _extract_guid(user)
    return None


async def _identity_probe(client: Any) -> tuple[str, str | None]:
    stored = str(getattr(client, "guid", "") or "").strip() or None
    probes: list[tuple[str, Any]] = []
    if callable(getattr(client, "get_me", None)):
        probes.append(("get_me", client.get_me))
    if stored and callable(getattr(client, "get_user_info", None)):
        probes.append(("get_user_info", lambda: client.get_user_info(stored)))
    if stored and callable(getattr(client, "get_abs_objects", None)):
        probes.append(("get_abs_objects", lambda: client.get_abs_objects([stored])))

    last_err: Exception | None = None
    for name, call in probes:
        try:
            result = await call()
            guid = _extract_guid(result)
            if guid:
                return name, guid
            if stored:
                return name, stored
        except Exception as exc:
            last_err = exc
            msg = str(exc).lower()
            if any(
                tok in msg
                for tok in (
                    "otp",
                    "login",
                    "sign in",
                    "unauthorized",
                    "invalid auth",
                    "session expired",
                    "requires login",
                    "pass_key",
                )
            ):
                raise
            continue
    if last_err is not None and stored is None:
        raise last_err
    return "connect_only", stored


async def _build_client_from_envelope(envelope: dict[str, str]) -> Any:
    from rubpy import Client
    from rubpy.sessions import StringSession

    string_session = StringSession()
    string_session.session = [
        envelope["phone_number"],
        envelope["auth"],
        envelope["guid"],
        envelope["user_agent"],
        envelope["private_key"],
    ]
    return Client(name=string_session, display_welcome=False)


async def _connect_authenticated(client: Any) -> None:
    from Crypto.PublicKey import RSA
    from Crypto.Signature import pkcs1_15
    from rubpy.crypto import Crypto as RubikaCrypto

    await client.connect()
    client.decode_auth = RubikaCrypto.decode_auth(client.auth) if client.auth else None
    client.import_key = (
        pkcs1_15.new(RSA.import_key(client.private_key.encode()))
        if client.private_key
        else None
    )


@dataclass(frozen=True)
class CandidateProofResult:
    proof_status: str
    error_code: str | None = None
    sanitized_message: str = ""
    returned_identity_present: bool = False
    identity_match: bool | None = None
    duration_ms: int = 0
    identity_guid: str | None = None

    @property
    def ok(self) -> bool:
        return self.proof_status == PROOF_PASS


class RubikaNetworkAdapter(Protocol):
    async def build_client(self, envelope: dict[str, str]) -> Any: ...
    async def connect(self, client: Any) -> None: ...
    async def probe_identity(self, client: Any) -> tuple[str, str | None]: ...
    async def disconnect(self, client: Any) -> None: ...


class DefaultRubikaNetworkAdapter:
    async def build_client(self, envelope: dict[str, str]) -> Any:
        return await _build_client_from_envelope(envelope)

    async def connect(self, client: Any) -> None:
        await _connect_authenticated(client)

    async def probe_identity(self, client: Any) -> tuple[str, str | None]:
        return await _identity_probe(client)

    async def disconnect(self, client: Any) -> None:
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


def _compare_identity(
    account: Account | None,
    proven_guid: str | None,
    expected_guid: str | None,
) -> tuple[bool, str | None, str | None]:
    """Return (matched, identity_guid_or_none, failure_code_or_none)."""
    cleaned = str(proven_guid or "").strip()
    if not cleaned:
        return False, None, IDENTITY_MISSING

    expected = str(expected_guid or "").strip() or None
    if expected and cleaned != expected:
        return False, cleaned, IDENTITY_MISMATCH

    if account is None:
        return True, cleaned, None

    bound = str(account.rubika_guid or "").strip() or None
    status = account.rubika_identity_status
    if bound is None or status in {None, RubikaIdentityStatus.UNBOUND}:
        return True, cleaned, None
    if bound != cleaned:
        return False, cleaned, IDENTITY_MISMATCH
    return True, cleaned, None


@dataclass
class RealRubikaCandidateProver:
    network: RubikaNetworkAdapter | None = None
    timeout_seconds: float | None = None

    def _timeout(self) -> float:
        if self.timeout_seconds is not None:
            return float(self.timeout_seconds)
        settings = get_settings()
        return float(getattr(settings, "RUBIKA_CANDIDATE_PROVE_TIMEOUT_SECONDS", 25) or 25)

    async def prove_detailed(
        self,
        db: Session,
        *,
        account_id: int,
        session_id: int,
        expected_guid: str | None,
    ) -> CandidateProofResult:
        started = time.perf_counter()
        net = self.network or DefaultRubikaNetworkAdapter()
        client: Any | None = None

        def _fail(
            code: str,
            sanitized: str,
            *,
            identity_present: bool = False,
            identity_match: bool | None = None,
            identity_guid: str | None = None,
        ) -> CandidateProofResult:
            return CandidateProofResult(
                proof_status=code,
                error_code=code,
                sanitized_message=sanitized,
                returned_identity_present=identity_present,
                identity_match=identity_match,
                duration_ms=int((time.perf_counter() - started) * 1000),
                identity_guid=identity_guid,
            )

        try:
            row = db.query(ChannelSession).filter(ChannelSession.id == int(session_id)).first()
            if row is None:
                return _fail(SESSION_MISSING, "candidate session row not found")

            try:
                plaintext = load_channel_session_plaintext(row)
            except (SessionDecryptionError, Exception) as exc:  # noqa: BLE001
                return _fail(SESSION_DECRYPT_FAILED, _sanitize_message(type(exc).__name__))

            try:
                envelope = parse_session_envelope(plaintext)
            except ValueError as exc:
                return _fail(SESSION_STRUCTURALLY_INVALID, _sanitize_message(str(exc)))

            account = db.query(Account).filter(Account.id == int(account_id)).first()

            async def _run() -> CandidateProofResult:
                nonlocal client
                client = await net.build_client(envelope)
                await net.connect(client)
                _method, proven_guid = await net.probe_identity(client)
                identity_present = bool(str(proven_guid or "").strip())
                matched, identity_guid, fail_code = _compare_identity(
                    account, proven_guid, expected_guid
                )
                if not matched:
                    return _fail(
                        fail_code or IDENTITY_MISMATCH,
                        _sanitize_message(fail_code or IDENTITY_MISMATCH),
                        identity_present=identity_present,
                        identity_match=False,
                        identity_guid=identity_guid,
                    )
                return CandidateProofResult(
                    proof_status=PROOF_PASS,
                    error_code=None,
                    sanitized_message="authenticated reconnect and identity verified",
                    returned_identity_present=True,
                    identity_match=True,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    identity_guid=identity_guid,
                )

            try:
                return await asyncio.wait_for(_run(), timeout=self._timeout())
            except asyncio.TimeoutError:
                return _fail(AUTH_RECONNECT_TIMEOUT, "reconnect proof timed out")
            except Exception as exc:  # noqa: BLE001
                return _fail(AUTH_RECONNECT_FAILED, _sanitize_message(type(exc).__name__))
        finally:
            if client is not None:
                try:
                    await net.disconnect(client)
                except Exception:  # noqa: BLE001
                    pass

    async def prove(
        self,
        db: Session,
        *,
        account_id: int,
        session_id: int,
        expected_guid: str | None,
    ):
        """L3 CandidateSessionProver protocol adapter."""
        from core_engine.services.rubika_login_state_machine import CandidateProveResult

        result = await self.prove_detailed(
            db,
            account_id=account_id,
            session_id=session_id,
            expected_guid=expected_guid,
        )
        if result.ok:
            return CandidateProveResult(ok=True, code=PROOF_PASS, identity_guid=result.identity_guid)
        return CandidateProveResult(
            ok=False,
            code=result.error_code or result.proof_status,
            identity_guid=result.identity_guid,
        )


def resolve_canonical_candidate_prover() -> RealRubikaCandidateProver:
    prover = RealRubikaCandidateProver()
    assert_prover_allowed_for_canonical(prover)
    return prover


def assert_prover_allowed_for_canonical(prover: Any) -> None:
    import os

    from core_engine.config import get_settings
    from core_engine.services.rubika_login_fake_provider import PassThroughCandidateProver

    if not bool(get_settings().RUBIKA_CANONICAL_SESSION_V1):
        return
    if not isinstance(prover, PassThroughCandidateProver):
        return
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    raise RuntimeError(
        "PassThroughCandidateProver is forbidden when RUBIKA_CANONICAL_SESSION_V1=true "
        "outside isolated tests. Use RealRubikaCandidateProver."
    )
