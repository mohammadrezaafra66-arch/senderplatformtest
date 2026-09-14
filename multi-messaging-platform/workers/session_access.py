"""Worker helpers for loading decrypted channel sessions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from core_engine.models import ChannelSession, SessionType
from core_engine.services.rubika_canonical_session import CanonicalSessionError
from core_engine.services.session_storage import load_channel_session_plaintext
from workers.errors import SessionInvalidError


def load_account_session_plaintext(
    db: Session,
    *,
    account_id: int,
    session_type: SessionType,
) -> bytes:
    """Load and decrypt a session for an account before platform use.

    Rubika: L7 cohort gating via ``load_rubika_runtime_session``
    (off / shadow / enforce). Non-Rubika platforms keep legacy max(id).
    """
    import logging

    if session_type == SessionType.RUBIKA_SESSION:
        from core_engine.services.rubika_canonical_runtime import (
            load_rubika_runtime_session,
        )

        try:
            selected = load_rubika_runtime_session(db, int(account_id))
        except CanonicalSessionError as exc:
            # Enforce fail-closed — do not fall back to max(id).
            raise SessionInvalidError(
                f"Canonical Rubika session unavailable ({exc.code})."
            ) from exc
        except Exception as exc:
            if type(exc).__name__ == "SessionInvalidError":
                raise
            raise SessionInvalidError("Failed to load Rubika session.") from exc
        logging.getLogger("workers.session_access").info(
            "event=rubika_runtime_session_selected account_id=%s session_id=%s "
            "source=%s mode=%s",
            selected.account_id,
            selected.session_id,
            selected.source,
            selected.mode,
        )
        return selected.plaintext

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
