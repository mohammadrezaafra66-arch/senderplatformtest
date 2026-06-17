"""Worker helpers for loading decrypted channel sessions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from core_engine.models import ChannelSession, SessionType
from core_engine.services.session_storage import load_channel_session_plaintext
from workers.errors import SessionInvalidError


def load_account_session_plaintext(
    db: Session,
    *,
    account_id: int,
    session_type: SessionType,
) -> bytes:
    """Load and decrypt the latest session for an account before platform use."""
    row = (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == account_id,
            ChannelSession.session_type == session_type,
        )
        .order_by(ChannelSession.id.desc())
        .first()
    )
    if row is None or not row.ciphertext:
        raise SessionInvalidError(
            f"No encrypted session found for account {account_id} ({session_type.value})."
        )

    try:
        return load_channel_session_plaintext(row)
    except Exception as exc:
        raise SessionInvalidError("Failed to decrypt channel session.") from exc
