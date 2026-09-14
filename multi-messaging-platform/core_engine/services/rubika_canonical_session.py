"""Canonical Rubika session loader (L1/L2).

Runtime must load ACTIVE sessions only — never max(id), never LEGACY_UNCLASSIFIED.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core_engine.models import (
    Account,
    ChannelSession,
    RubikaIdentityStatus,
    RubikaSessionStatus,
    SessionType,
)
from core_engine.services.crypto import SessionDecryptionError
from core_engine.services.rubika_user_session import parse_session_envelope
from core_engine.services.session_storage import load_channel_session_plaintext

logger = logging.getLogger("core_engine.services.rubika_canonical_session")

NO_ACTIVE_SESSION = "NO_ACTIVE_SESSION"
MULTIPLE_ACTIVE_SESSIONS = "MULTIPLE_ACTIVE_SESSIONS"
SESSION_DECRYPT_FAILED = "SESSION_DECRYPT_FAILED"
SESSION_STRUCTURALLY_INVALID = "SESSION_STRUCTURALLY_INVALID"
IDENTITY_BINDING_MISSING = "IDENTITY_BINDING_MISSING"
IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
ACCOUNT_MISSING = "ACCOUNT_MISSING"
WRONG_PLATFORM = "WRONG_PLATFORM"


class CanonicalSessionError(Exception):
    """Typed failure from load_canonical_rubika_session."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class CanonicalRubikaSession:
    account_id: int
    session_id: int
    identity_guid: str | None
    envelope: dict[str, str]
    validated_at: Any = None
    plaintext: bytes | None = None  # in-memory only; never log/persist via API


def _active_rubika_rows(db: Session, account_id: int) -> list[ChannelSession]:
    return (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == int(account_id),
            ChannelSession.session_type == SessionType.RUBIKA_SESSION,
            ChannelSession.session_status == RubikaSessionStatus.ACTIVE,
        )
        .all()
    )


def load_canonical_rubika_session(
    db: Session,
    account_id: int,
    *,
    require_identity_binding: bool = True,
    return_plaintext: bool = False,
) -> CanonicalRubikaSession:
    """Load the sole ACTIVE Rubika session for an account.

    Raises CanonicalSessionError with the codes defined in L1.
    Does not fall back to max(id) or LEGACY_UNCLASSIFIED.
    """
    from core_engine.models import PlatformType

    account = db.query(Account).filter(Account.id == int(account_id)).first()
    if account is None:
        raise CanonicalSessionError(ACCOUNT_MISSING, f"account {account_id} not found")
    if account.platform != PlatformType.RUBIKA:
        raise CanonicalSessionError(WRONG_PLATFORM, "account is not Rubika")

    rows = _active_rubika_rows(db, account_id)
    if len(rows) == 0:
        raise CanonicalSessionError(NO_ACTIVE_SESSION)
    if len(rows) > 1:
        raise CanonicalSessionError(
            MULTIPLE_ACTIVE_SESSIONS,
            f"account {account_id} has {len(rows)} ACTIVE rubika sessions",
        )

    row = rows[0]
    try:
        plaintext = load_channel_session_plaintext(row)
    except SessionDecryptionError as exc:
        raise CanonicalSessionError(SESSION_DECRYPT_FAILED, type(exc).__name__) from exc
    except Exception as exc:  # noqa: BLE001
        raise CanonicalSessionError(SESSION_DECRYPT_FAILED, type(exc).__name__) from exc

    try:
        envelope = parse_session_envelope(plaintext)
    except ValueError as exc:
        raise CanonicalSessionError(SESSION_STRUCTURALLY_INVALID, str(exc)[:120]) from exc

    guid = str(envelope.get("guid") or row.identity_guid or "").strip() or None

    if require_identity_binding:
        bound = str(account.rubika_guid or "").strip() or None
        status = account.rubika_identity_status
        if bound is None or status in {None, RubikaIdentityStatus.UNBOUND}:
            raise CanonicalSessionError(IDENTITY_BINDING_MISSING)
        if guid and bound != guid:
            raise CanonicalSessionError(IDENTITY_MISMATCH)

    return CanonicalRubikaSession(
        account_id=int(account_id),
        session_id=int(row.id),
        identity_guid=guid,
        envelope=envelope,
        validated_at=row.validated_at,
        plaintext=plaintext if return_plaintext else None,
    )


def count_active_rubika_sessions(db: Session, account_id: int) -> int:
    return len(_active_rubika_rows(db, account_id))
