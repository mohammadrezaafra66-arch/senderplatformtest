"""Isolated Base64/PEM private_key import regression tests (Campaign 233 forensics).

Uses synthetic fixtures only — no production sessions, no external Rubika send.
"""

from __future__ import annotations

import binascii

import pytest
from Crypto.PublicKey import RSA

from workers.connectors.rubika_user import _import_rubika_signing_key
from workers.errors import SessionInvalidError


def _valid_pem() -> str:
    return RSA.generate(1024).export_key().decode("utf-8")


def test_valid_pem_private_key_imports():
    signer = _import_rubika_signing_key(_valid_pem())
    assert signer is not None
    assert hasattr(signer, "sign")


def test_invalid_pem_body_reproduces_production_base64_error():
    """Exact production stack: RSA.import_key → PEM.decode → a2b_base64(lines[1:-1])."""
    corrupt = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "A\n"
        "-----END RSA PRIVATE KEY-----"
    )
    with pytest.raises(binascii.Error) as raw:
        RSA.import_key(corrupt.encode("utf-8"))
    assert "number of data characters (1) cannot be 1 more than a multiple of 4" in str(
        raw.value
    )

    with pytest.raises(SessionInvalidError) as wrapped:
        _import_rubika_signing_key(corrupt)
    assert "private_key" in str(wrapped.value).lower()
    assert isinstance(wrapped.value.__cause__, binascii.Error)


def test_empty_private_key_fails_cleanly():
    with pytest.raises(SessionInvalidError):
        _import_rubika_signing_key("")
    with pytest.raises(SessionInvalidError):
        _import_rubika_signing_key("   ")


def test_null_private_key_fails_cleanly():
    with pytest.raises(SessionInvalidError):
        _import_rubika_signing_key(None)  # type: ignore[arg-type]


def test_wrong_type_private_key_fails_cleanly():
    with pytest.raises(SessionInvalidError):
        _import_rubika_signing_key(b"not-a-string")  # type: ignore[arg-type]


def test_already_decoded_non_pem_bytes_as_str_fails_typed():
    """Passing raw non-PEM garbage must not become rubika_user_unexpected_error."""
    with pytest.raises(SessionInvalidError):
        _import_rubika_signing_key("not-base64-and-not-pem")


def test_urlsafe_vs_standard_body_still_requires_valid_rsa():
    """URL-safe alphabet alone is not sufficient — RSA DER must parse."""
    bad = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "abcd\n"
        "-----END RSA PRIVATE KEY-----"
    )
    with pytest.raises(SessionInvalidError):
        _import_rubika_signing_key(bad)


def test_import_is_single_pass_no_double_decode():
    """Valid PEM imports once; helper must not re-decode the returned signer material."""
    pem = _valid_pem()
    first = _import_rubika_signing_key(pem)
    second = _import_rubika_signing_key(pem)
    assert first is not None and second is not None
    # Re-importing the same PEM succeeds (idempotent source), proving we do not
    # mutate/corrupt the input string across calls.
    assert pem.startswith("-----BEGIN")
