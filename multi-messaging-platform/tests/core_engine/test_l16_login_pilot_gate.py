"""L16 — account-scoped L3 login pilot gate (isolated)."""

from __future__ import annotations

import pytest

from core_engine.config import get_settings
from core_engine.services.rubika_login_state_machine import (
    account_uses_l3_login,
    assert_legacy_login_allowed,
)
from core_engine.services.rubika_user_session import RubikaLoginError


def test_pilot_account_uses_l3_while_global_v1_off(monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_V1", "false")
    monkeypatch.setenv("RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS", "14")
    get_settings.cache_clear()
    assert account_uses_l3_login(14) is True
    assert account_uses_l3_login(13) is False
    assert account_uses_l3_login(79) is False


def test_legacy_blocked_for_pilot_only(monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_V1", "false")
    monkeypatch.setenv("RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS", "14")
    get_settings.cache_clear()
    with pytest.raises(RubikaLoginError):
        assert_legacy_login_allowed(14)
    assert_legacy_login_allowed(79)  # non-pilot unchanged


def test_global_v1_still_blocks_all_legacy(monkeypatch):
    monkeypatch.setenv("RUBIKA_CANONICAL_SESSION_V1", "true")
    monkeypatch.setenv("RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS", "")
    get_settings.cache_clear()
    with pytest.raises(RubikaLoginError):
        assert_legacy_login_allowed(79)
