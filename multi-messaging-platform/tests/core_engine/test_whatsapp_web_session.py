import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import Account, AccountStatus, ChannelSession, PlatformType, SessionType
from core_engine.services.whatsapp_web_session import (
    build_whatsapp_web_status,
    load_whatsapp_web_session,
    parse_whatsapp_web_metadata,
    profile_dir_has_browser_data,
    resolve_whatsapp_profile_dir,
    resolve_whatsapp_runtime_profile_dir,
    store_whatsapp_web_session,
)


@pytest.fixture(autouse=True)
def session_secret(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_resolve_whatsapp_profile_dir():
    path = resolve_whatsapp_profile_dir(7, profile_root="tmp/profiles")
    assert path == resolve_whatsapp_profile_dir(7, profile_root="tmp/profiles")
    assert str(path).endswith("account-7")


def test_profile_dir_has_browser_data(tmp_path):
    assert profile_dir_has_browser_data(tmp_path) is False
    (tmp_path / "Default").mkdir()
    assert profile_dir_has_browser_data(tmp_path) is True


def test_resolve_whatsapp_runtime_profile_dir_prefers_host_profile(
    tmp_path, monkeypatch
):
    host_profile = tmp_path / "SenderPlatform" / "mmp-whatsapp" / "account-7"
    (host_profile / "Default").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    resolved = resolve_whatsapp_runtime_profile_dir(
        7,
        str(tmp_path / "docker-storage" / "account-7"),
        profile_root=str(tmp_path / "docker-storage"),
    )
    assert Path(resolved) == host_profile.resolve()


def test_resolve_whatsapp_runtime_profile_dir_prefers_host_over_docker_copy(
    tmp_path, monkeypatch
):
    host_profile = tmp_path / "SenderPlatform" / "mmp-whatsapp" / "account-7"
    docker_profile = tmp_path / "docker-storage" / "account-7"
    (host_profile / "Default").mkdir(parents=True)
    (docker_profile / "Default").mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("core_engine.services.whatsapp_web_session.os.name", "nt")

    resolved = resolve_whatsapp_runtime_profile_dir(
        7,
        str(docker_profile),
        profile_root=str(tmp_path / "docker-storage"),
    )
    assert Path(resolved) == host_profile.resolve()


def test_parse_whatsapp_web_metadata():
    payload = json.dumps(
        {
            "version": 1,
            "profile_dir": "storage/browser_profiles/whatsapp/account-1",
            "linked": True,
            "phone": "+989121234567",
            "linked_at": "2026-01-01T00:00:00+00:00",
        }
    ).encode("utf-8")
    metadata = parse_whatsapp_web_metadata(payload)
    assert metadata.linked is True
    assert metadata.phone == "+989121234567"
    assert metadata.profile_dir.endswith("account-1")


def test_store_and_load_whatsapp_web_session(pg_session_factory):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.WHATSAPP,
        phone_number="09120000099",
        label="WA Web",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.flush()

    profile_dir = tmp_profile = resolve_whatsapp_profile_dir(account.id)
    store_whatsapp_web_session(
        session,
        account_id=account.id,
        linked=True,
        phone=account.phone_number,
        profile_dir=profile_dir,
    )
    session.commit()

    loaded = load_whatsapp_web_session(session, account.id)
    assert loaded is not None
    assert loaded.linked is True
    assert loaded.phone == account.phone_number
    assert loaded.profile_dir == tmp_profile.as_posix()

    session.query(ChannelSession).filter(ChannelSession.account_id == account.id).delete()
    session.query(Account).filter(Account.id == account.id).delete()
    session.commit()
    session.close()


def test_build_whatsapp_web_status_without_profile(pg_session_factory):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.WHATSAPP,
        phone_number="09120000100",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.commit()
    account_id = account.id

    status = build_whatsapp_web_status(session, account_id)
    assert status["linked"] is False
    assert status["needs_qr"] is True
    assert status["profile_exists"] is False

    session.query(Account).filter(Account.id == account_id).delete()
    session.commit()
    session.close()
