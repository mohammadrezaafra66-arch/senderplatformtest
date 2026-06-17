"""Encrypted storage and retrieval for channel session credentials."""

from __future__ import annotations

import base64
from pathlib import Path

from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import ChannelSession, SessionType
from core_engine.services.crypto import SessionDecryptionError, decrypt, encrypt


def get_session_key_bytes() -> bytes:
    settings = get_settings()
    return settings.SESSION_SECRET.encode("utf-8")


def encrypt_session_data(data: bytes, *, key: bytes | None = None) -> bytes:
    encryption_key = key or get_session_key_bytes()
    return encrypt(data, encryption_key)


def decrypt_session_data(token: bytes, *, key: bytes | None = None) -> bytes:
    encryption_key = key or get_session_key_bytes()
    return decrypt(token, encryption_key)


def encode_ciphertext_blob(encrypted: bytes) -> str:
    return base64.urlsafe_b64encode(encrypted).decode("ascii")


def decode_ciphertext_blob(ciphertext: str) -> bytes:
    return base64.urlsafe_b64decode(ciphertext.encode("ascii"))


def store_channel_session(
    db: Session,
    *,
    account_id: int,
    session_type: SessionType,
    plaintext: bytes | str,
    key_version: int = 1,
) -> ChannelSession:
    """Persist encrypted session material in channel_sessions.ciphertext."""
    if isinstance(plaintext, str):
        plaintext_bytes = plaintext.encode("utf-8")
    else:
        plaintext_bytes = plaintext

    encrypted = encrypt_session_data(plaintext_bytes)
    ciphertext = encode_ciphertext_blob(encrypted)

    row = ChannelSession(
        account_id=account_id,
        session_type=session_type,
        ciphertext=ciphertext,
        key_version=key_version,
    )
    db.add(row)
    db.flush()
    return row


def load_channel_session_plaintext(channel_session: ChannelSession) -> bytes:
    """Decrypt ciphertext stored on a ChannelSession row."""
    if not channel_session.ciphertext:
        raise SessionDecryptionError("Channel session has no ciphertext.")

    token = decode_ciphertext_blob(channel_session.ciphertext)
    return decrypt_session_data(token)


def write_encrypted_session_file(path: str | Path, plaintext: bytes | str) -> Path:
    """Write encrypted session bytes to disk (never plaintext)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(plaintext, str):
        plaintext_bytes = plaintext.encode("utf-8")
    else:
        plaintext_bytes = plaintext

    encrypted = encrypt_session_data(plaintext_bytes)
    target.write_bytes(encrypted)
    return target


def read_encrypted_session_file(path: str | Path) -> bytes:
    """Read and decrypt session bytes from an encrypted file."""
    encrypted = Path(path).read_bytes()
    return decrypt_session_data(encrypted)
