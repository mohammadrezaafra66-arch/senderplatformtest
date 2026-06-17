"""Fernet-based encryption helpers for channel session secrets."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SessionDecryptionError(Exception):
    """Raised when session ciphertext cannot be decrypted."""


def generate_key() -> bytes:
    """Generate a new Fernet key (store in SESSION_SECRET for deployment)."""
    return Fernet.generate_key()


def encrypt(data: bytes, key: bytes) -> bytes:
    return Fernet(key).encrypt(data)


def decrypt(token: bytes, key: bytes) -> bytes:
    try:
        return Fernet(key).decrypt(token)
    except InvalidToken as exc:
        raise SessionDecryptionError("Invalid session ciphertext or encryption key.") from exc
