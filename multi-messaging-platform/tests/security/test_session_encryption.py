import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import SessionType
from core_engine.services.crypto import (
    SessionDecryptionError,
    decrypt,
    encrypt,
    generate_key,
)
from core_engine.services.session_storage import (
    load_channel_session_plaintext,
    read_encrypted_session_file,
    store_channel_session,
    write_encrypted_session_file,
)


@pytest.fixture
def fernet_key() -> bytes:
    return generate_key()


def test_encrypt_decrypt_roundtrip(fernet_key):
    plaintext = b"telegram-session-token-12345"
    token = encrypt(plaintext, fernet_key)
    assert decrypt(token, fernet_key) == plaintext


def test_decrypt_with_wrong_key_raises(fernet_key):
    token = encrypt(b"secret", fernet_key)
    with pytest.raises(SessionDecryptionError):
        decrypt(token, generate_key())


def test_encrypted_payload_differs_from_plaintext(fernet_key):
    plaintext = b"plain-session-material"
    token = encrypt(plaintext, fernet_key)
    assert token != plaintext
    assert plaintext.decode("utf-8") not in token.decode("utf-8", errors="ignore")


def test_channel_session_ciphertext_is_not_plaintext(sqlite_session_factory):
    db = sqlite_session_factory()
    secret = "mtproto-session-string-value"
    row = store_channel_session(
        db,
        account_id=1,
        session_type=SessionType.MTPROTO_SESSION,
        plaintext=secret,
    )
    db.commit()

    assert row.ciphertext is not None
    assert secret not in row.ciphertext
    assert load_channel_session_plaintext(row).decode("utf-8") == secret


def test_encrypted_session_file_is_not_plaintext(tmp_path):
    secret = "browser-profile-session-cookie"
    file_path = tmp_path / "sessions" / "account-1.session"
    write_encrypted_session_file(file_path, secret)

    raw = file_path.read_bytes()
    assert secret.encode("utf-8") not in raw
    assert read_encrypted_session_file(file_path).decode("utf-8") == secret


def test_settings_require_session_secret(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        get_settings()
    get_settings.cache_clear()


@pytest.fixture
def sqlite_session_factory(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from core_engine.database import Base
    from core_engine.models import Account, AccountStatus, ChannelSession, PlatformType

    test_key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", test_key)
    get_settings.cache_clear()

    engine = create_engine("sqlite:///:memory:")
    tables = [
        Account.__table__,
        ChannelSession.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine)

    def _factory():
        session = SessionLocal()
        account = Account(
            id=1,
            platform=PlatformType.TELEGRAM,
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        session.commit()
        return session

    yield _factory
    engine.dispose()
    get_settings.cache_clear()
