"""Tests for unified account session wiring (Phase 8.6)."""

import json

import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import Account, AccountStatus, PlatformType, SessionType
from core_engine.services.account_session_wiring import (
    build_account_session_status,
    build_deploy_readiness,
    evaluate_account_session_readiness,
    register_api_token_session,
    required_session_type,
)
from core_engine.services.session_storage import store_channel_session
from core_engine.services.whatsapp_web_session import store_whatsapp_web_session


@pytest.fixture(autouse=True)
def session_secret(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def bale_account(pg_session_factory):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.BALE,
        phone_number="09121112233",
        label="Wiring Bale",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.commit()
    account_id = account.id
    session.close()
    yield account_id
    session = pg_session_factory()
    from core_engine.models import ChannelSession

    session.query(ChannelSession).filter(ChannelSession.account_id == account_id).delete()
    session.query(Account).filter(Account.id == account_id).delete()
    session.commit()
    session.close()


def test_required_session_type_bot_platforms():
    assert required_session_type(PlatformType.BALE) == SessionType.API_TOKEN
    assert required_session_type(PlatformType.TELEGRAM) == SessionType.API_TOKEN
    assert required_session_type(PlatformType.RUBIKA) == SessionType.API_TOKEN


def test_required_session_type_whatsapp_web():
    assert (
        required_session_type(PlatformType.WHATSAPP, whatsapp_delivery_mode="web")
        == SessionType.BROWSER_PROFILE
    )


def test_required_session_type_whatsapp_cloud():
    assert (
        required_session_type(PlatformType.WHATSAPP, whatsapp_delivery_mode="cloud_api")
        == SessionType.API_TOKEN
    )


def test_register_api_token_session_plain(pg_session_factory, bale_account):
    session = pg_session_factory()
    account = session.get(Account, bale_account)
    row = register_api_token_session(
        session,
        account=account,
        session_payload="MY_BALE_BOT_TOKEN",
    )
    session.commit()
    assert row.session_type == SessionType.API_TOKEN
    status = build_account_session_status(session, account)
    assert status["session_registered"] is True
    assert status["ready_for_delivery"] is True
    session.close()


def test_register_api_token_session_json(pg_session_factory, bale_account):
    session = pg_session_factory()
    account = session.get(Account, bale_account)
    payload = json.dumps({"bot_token": "JSON_TOKEN"})
    register_api_token_session(session, account=account, session_payload=payload)
    session.commit()
    readiness = evaluate_account_session_readiness(session, account)
    assert readiness.ready is True
    session.close()


def test_evaluate_readiness_missing_session(pg_session_factory, bale_account):
    session = pg_session_factory()
    account = session.get(Account, bale_account)
    readiness = evaluate_account_session_readiness(session, account)
    assert readiness.ready is False
    assert readiness.error == "session_missing"
    session.close()


def test_build_deploy_readiness(pg_session_factory, bale_account):
    session = pg_session_factory()
    report = build_deploy_readiness(session)
    assert report["phase"] == "9.2"
    assert report["accounts_total"] >= 1
    assert len(report["worker_services"]) >= 4
    session.close()


def test_whatsapp_web_status_in_session_report(pg_session_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_WEB_PROFILE_ROOT", str(tmp_path))
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.WHATSAPP,
        phone_number="09120001111",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.flush()
    profile_dir = tmp_path / f"account-{account.id}"
    profile_dir.mkdir()
    (profile_dir / "Default").mkdir()
    store_whatsapp_web_session(
        session,
        account_id=account.id,
        linked=True,
        phone="09120001111",
        profile_dir=profile_dir,
    )
    session.commit()

    status = build_account_session_status(session, account, whatsapp_delivery_mode="web")
    assert status["delivery_mode"] == "web"
    assert status["linked"] is True
    assert status["ready_for_delivery"] is True

    from core_engine.models import ChannelSession

    session.query(ChannelSession).filter(ChannelSession.account_id == account.id).delete()
    session.query(Account).filter(Account.id == account.id).delete()
    session.commit()
    session.close()
