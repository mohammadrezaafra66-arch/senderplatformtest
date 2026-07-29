"""Tests for the Rubika status bot's account-selection lifecycle.

No PostgreSQL, Redis, Rubika or OpenAI is touched: the pool lookup and the
client factory are replaced, and the retry interval is shrunk so nothing sleeps
for a meaningful amount of time.
"""

import asyncio
import logging

import pytest

from workers import rubika_status_bot as status_bot
from workers.errors import SessionInvalidError

FAST_RETRY = 0.01


@pytest.fixture(autouse=True)
def fast_retry(monkeypatch):
    """Keep every wait in these tests sub-millisecond."""
    monkeypatch.setattr(status_bot, "_ACCOUNT_RETRY_SECONDS", FAST_RETRY)
    monkeypatch.setattr(status_bot, "_POLL_INTERVAL", FAST_RETRY)


def _bot() -> status_bot.RubikaStatusBot:
    bot = status_bot.RubikaStatusBot.__new__(status_bot.RubikaStatusBot)
    bot.settings = None
    bot.client = None
    bot.account_id = None
    bot._rubino_profile_id = None
    bot._shutdown = asyncio.Event()
    return bot


def test_positive_int_env_rejects_non_positive(monkeypatch):
    monkeypatch.setenv("RUBIKA_STATUS_ACCOUNT_RETRY_SECONDS", "0")
    assert status_bot._positive_int_env("RUBIKA_STATUS_ACCOUNT_RETRY_SECONDS", 60) == 60

    monkeypatch.setenv("RUBIKA_STATUS_ACCOUNT_RETRY_SECONDS", "-5")
    assert status_bot._positive_int_env("RUBIKA_STATUS_ACCOUNT_RETRY_SECONDS", 60) == 60

    monkeypatch.setenv("RUBIKA_STATUS_ACCOUNT_RETRY_SECONDS", "not-a-number")
    assert status_bot._positive_int_env("RUBIKA_STATUS_ACCOUNT_RETRY_SECONDS", 60) == 60

    monkeypatch.setenv("RUBIKA_STATUS_ACCOUNT_RETRY_SECONDS", "7")
    assert status_bot._positive_int_env("RUBIKA_STATUS_ACCOUNT_RETRY_SECONDS", 60) == 7


@pytest.mark.asyncio
async def test_missing_account_does_not_crash_and_retries(monkeypatch, caplog):
    """Test 1 — an empty pool is an operational state, not a RuntimeError."""
    bot = _bot()
    calls = []

    def fake_select():
        calls.append(1)
        if len(calls) >= 3:
            bot.request_shutdown()
        return None

    monkeypatch.setattr(bot, "_select_status_account_id", fake_select)

    with caplog.at_level(logging.WARNING):
        result = await bot.wait_for_status_account()

    assert result is None
    assert len(calls) >= 2, "should have retried instead of giving up"
    assert "No active Rubika account with phase='status' is available" in caplog.text
    assert "retrying in" in caplog.text


@pytest.mark.asyncio
async def test_account_appearing_later_is_picked_up(monkeypatch):
    """Test 2 — None, None, 123 resolves to 123 without a manual restart."""
    bot = _bot()
    sequence = [None, None, 123]
    calls = []

    def fake_select():
        calls.append(1)
        return sequence[len(calls) - 1]

    monkeypatch.setattr(bot, "_select_status_account_id", fake_select)

    assert await bot.wait_for_status_account() == 123
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_no_client_is_built_while_pool_is_empty(monkeypatch):
    """Test 3 — nothing external is touched during the wait."""
    bot = _bot()
    created = []

    async def fake_client(account_id, db=None):
        created.append(account_id)
        raise AssertionError("client must not be built without an account")

    monkeypatch.setattr(status_bot, "load_rubika_user_client", fake_client)

    calls = []

    def fake_select():
        calls.append(1)
        if len(calls) >= 3:
            bot.request_shutdown()
        return None

    monkeypatch.setattr(bot, "_select_status_account_id", fake_select)

    assert await bot.wait_for_status_account() is None
    assert created == []


@pytest.mark.asyncio
async def test_available_account_is_selected_without_waiting(monkeypatch, caplog):
    """Test 4 — an account present up front skips the missing-account delay."""
    bot = _bot()
    monkeypatch.setattr(bot, "_select_status_account_id", lambda: 42)

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        return False

    monkeypatch.setattr(bot, "_sleep_or_shutdown", fake_sleep)

    with caplog.at_level(logging.INFO):
        assert await bot.wait_for_status_account() == 42

    assert slept == [], "no delay should be taken when an account exists"
    assert "Rubika status account selected account_id=42" in caplog.text
    assert "No active Rubika account" not in caplog.text


@pytest.mark.asyncio
async def test_shutdown_while_waiting_exits_promptly(monkeypatch):
    """Test 5 — a shutdown request wakes the wait instead of running it out."""
    bot = _bot()
    monkeypatch.setattr(status_bot, "_ACCOUNT_RETRY_SECONDS", 30)
    monkeypatch.setattr(bot, "_select_status_account_id", lambda: None)

    created = []

    async def fake_client(account_id, db=None):
        created.append(account_id)

    monkeypatch.setattr(status_bot, "load_rubika_user_client", fake_client)

    task = asyncio.create_task(bot.wait_for_status_account())
    await asyncio.sleep(0)
    bot.request_shutdown()

    result = await asyncio.wait_for(task, timeout=2)

    assert result is None
    assert created == []
    assert bot.client is None


@pytest.mark.asyncio
async def test_database_error_is_not_reported_as_missing_account(monkeypatch, caplog):
    """Test 6 — a failing query logs an exception and retries, it is not None."""
    bot = _bot()
    calls = []

    def failing_select():
        calls.append(1)
        if len(calls) >= 2:
            bot.request_shutdown()
        raise RuntimeError("connection refused")

    monkeypatch.setattr(bot, "_select_status_account_id", failing_select)

    with caplog.at_level(logging.WARNING):
        result = await bot.wait_for_status_account()

    assert result is None
    assert len(calls) >= 2, "should retry rather than spin or abort"
    assert "reading the account pool failed" in caplog.text
    assert "connection refused" in caplog.text
    assert "No active Rubika account" not in caplog.text, (
        "a database failure must not masquerade as an empty pool"
    )


@pytest.mark.asyncio
async def test_invalid_session_returns_to_account_selection(monkeypatch, caplog):
    """Test 7 — SessionInvalidError marks the account and re-enters the wait."""
    bot = _bot()
    selections = [7, 7]

    def fake_select():
        return selections.pop(0) if selections else None

    monkeypatch.setattr(bot, "_select_status_account_id", fake_select)

    marked = []
    monkeypatch.setattr(bot, "_mark_account_failed", lambda exc: marked.append(str(exc)))

    sessions = []

    async def failing_session():
        sessions.append(bot.account_id)
        if len(sessions) >= 2:
            bot.request_shutdown()
        raise SessionInvalidError("session expired")

    monkeypatch.setattr(bot, "_run_session", failing_session)

    with caplog.at_level(logging.WARNING):
        await bot.start()

    assert sessions == [7, 7], "should have retried with a freshly selected account"
    assert marked == ["session expired", "session expired"]
    assert "waiting for another status account" in caplog.text
    assert bot.client is None


@pytest.mark.asyncio
async def test_selected_account_is_not_queried_again_before_session_start(monkeypatch):
    """The account chosen during the wait is reused, never re-queried."""
    bot = _bot()
    selections = []

    def fake_select():
        selections.append(1)
        return 123

    monkeypatch.setattr(bot, "_select_status_account_id", fake_select)

    loaded = []

    class FakeClient:
        async def connect(self):
            return None

        async def disconnect(self):
            return None

    async def fake_client(account_id, db=None):
        loaded.append(account_id)
        return FakeClient()

    monkeypatch.setattr(status_bot, "load_rubika_user_client", fake_client)
    monkeypatch.setattr(
        "core_engine.services.redis_client.get_redis_client", lambda: None
    )

    async def fake_cycle(redis):
        bot.request_shutdown()
        return {"likes": 0, "comments": 0}

    async def fake_publish():
        return {"published": 0}

    monkeypatch.setattr(bot, "_run_like_and_comment_cycle", fake_cycle)
    monkeypatch.setattr(bot, "_run_publish_cycle", fake_publish)

    await bot.start()

    assert len(selections) == 1, "the pool must be queried exactly once"
    assert loaded == [123], "the session must use the account chosen during the wait"
    assert bot.account_id == 123


@pytest.mark.asyncio
async def test_run_session_requires_preselected_account(monkeypatch):
    """Calling a session without a chosen account fails before any client is built."""
    bot = _bot()
    bot.account_id = None

    loaded = []

    async def fake_client(account_id, db=None):
        loaded.append(account_id)

    monkeypatch.setattr(status_bot, "load_rubika_user_client", fake_client)

    with pytest.raises(RuntimeError, match="without a selected status account"):
        await bot._run_session()

    assert loaded == []
    assert bot.client is None


@pytest.mark.asyncio
async def test_session_client_is_disconnected_on_exit(monkeypatch):
    """The client built for a session is always torn down, exactly once."""
    bot = _bot()
    disconnects = []

    class FakeClient:
        async def connect(self):
            return None

        async def disconnect(self):
            disconnects.append(1)

    async def fake_client(account_id, db=None):
        return FakeClient()

    monkeypatch.setattr(status_bot, "load_rubika_user_client", fake_client)
    monkeypatch.setattr(
        "core_engine.services.redis_client.get_redis_client", lambda: None
    )

    async def fake_cycle(redis):
        bot.request_shutdown()
        return {"likes": 0, "comments": 0}

    async def fake_publish():
        return {"published": 0}

    monkeypatch.setattr(bot, "_run_like_and_comment_cycle", fake_cycle)
    monkeypatch.setattr(bot, "_run_publish_cycle", fake_publish)

    bot.account_id = 5
    await bot._run_session()

    assert disconnects == [1]
    assert bot.client is None
