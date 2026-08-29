"""R3 — worker safety: execution gate, pause/kill truthy parse, identity fail-fast."""

from __future__ import annotations

import json

import pytest

from workers.base_worker import WorkerExecutionDisabled
from workers.config import WorkerSettings, get_worker_settings
from workers.factory import WorkerIdentityConflict, build_worker
from workers.redis_flags import (
    parse_redis_truthy,
    set_account_pause,
    set_system_kill_switch,
)
from workers.redis_keys import account_pause_key, kill_switch_key, queue_key
from workers.rubika_worker import RubikaWorker


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.lpop_calls = 0
        self.get_calls: list[str] = []

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def get(self, key: str) -> str | None:
        self.get_calls.append(key)
        return self.kv.get(key)

    async def set(self, key: str, value: str) -> bool:
        self.kv[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.kv.pop(key, None) is not None else 0

    async def lpop(self, key: str) -> str | None:
        self.lpop_calls += 1
        items = self.lists.get(key) or []
        if not items:
            return None
        return items.pop(0)

    async def lpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(
        "workers.base_worker.Redis.from_url",
        lambda *args, **kwargs: fake,
    )
    return fake


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("", False),
        (None, False),
        (True, True),
        (False, False),
    ],
)
def test_parse_redis_truthy_contract(raw, expected):
    assert parse_redis_truthy(raw) is expected


@pytest.mark.asyncio
async def test_execution_disabled_zero_connect_lpop(fake_redis):
    worker = RubikaWorker(
        account_id=12,
        redis_url="redis://test",
        database_url="postgresql://test",
        execution_enabled=False,
    )
    q = queue_key("rubika", 12)
    fake_redis.lists[q] = [json.dumps({"message_id": 1})]

    with pytest.raises(WorkerExecutionDisabled):
        await worker.connect()
    assert worker._connect_count == 0

    await worker.run_once()
    assert fake_redis.lpop_calls == 0
    assert worker._lpop_count == 0
    assert len(fake_redis.lists[q]) == 1


@pytest.mark.asyncio
async def test_kill_switch_one_blocks_lpop(fake_redis):
    worker = RubikaWorker(
        account_id=12,
        redis_url="redis://test",
        database_url="postgresql://test",
        execution_enabled=True,
    )
    q = queue_key("rubika", 12)
    fake_redis.lists[q] = [json.dumps({"x": 1})]
    await set_system_kill_switch(fake_redis, enabled=True)
    # Writer stores canonical "true"; also prove "1" is honored by reader path.
    fake_redis.kv[kill_switch_key()] = "1"

    await worker.connect()
    await worker.run_once()
    assert fake_redis.lpop_calls == 0
    assert len(fake_redis.lists[q]) == 1


@pytest.mark.asyncio
async def test_account_pause_one_blocks_lpop(fake_redis):
    worker = RubikaWorker(
        account_id=79,
        redis_url="redis://test",
        database_url="postgresql://test",
        execution_enabled=True,
    )
    q = queue_key("rubika", 79)
    fake_redis.lists[q] = [json.dumps({"x": 1})]
    await set_account_pause(fake_redis, 79, paused=True)
    fake_redis.kv[account_pause_key(79)] = "1"

    await worker.connect()
    await worker.run_once()
    assert fake_redis.lpop_calls == 0
    assert len(fake_redis.lists[q]) == 1


def test_build_worker_refuses_when_execution_disabled(monkeypatch):
    monkeypatch.setenv("WORKER_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("WORKER_PLATFORM", "rubika")
    monkeypatch.setenv("WORKER_ACCOUNT_ID", "12")
    get_worker_settings.cache_clear()
    try:
        with pytest.raises(WorkerExecutionDisabled):
            build_worker()
    finally:
        get_worker_settings.cache_clear()


def test_build_worker_identity_conflict_missing_account(monkeypatch, pg_session_factory):
    # Ensure DB session factory is initialized for the app.
    session = pg_session_factory()
    session.close()

    monkeypatch.setenv("WORKER_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("WORKER_PLATFORM", "rubika")
    monkeypatch.setenv("WORKER_ACCOUNT_ID", "999999001")
    get_worker_settings.cache_clear()
    try:
        with pytest.raises(WorkerIdentityConflict):
            build_worker()
    finally:
        get_worker_settings.cache_clear()


def test_worker_settings_exposes_execution_flag(monkeypatch):
    monkeypatch.setenv("WORKER_EXECUTION_ENABLED", "false")
    get_worker_settings.cache_clear()
    try:
        cfg = get_worker_settings()
        assert cfg.WORKER_EXECUTION_ENABLED is False
        assert "WORKER_EXECUTION_ENABLED" in WorkerSettings.model_fields
    finally:
        get_worker_settings.cache_clear()
