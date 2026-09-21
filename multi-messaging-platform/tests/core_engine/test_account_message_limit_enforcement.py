"""Optional per-account hourly/daily caps: helpers, quota Lua sentinel, capacity.

No live Redis/Postgres. Fake Redis only. Count lifecycle/env caps are not used.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core_engine.services.account_message_limits import (
    UNLIMITED_REDIS_SENTINEL,
    InvalidAccountMessageLimit,
    is_unlimited_message_limit,
    normalize_account_message_limit,
    redis_cap_arg,
    remaining_for_limit,
    resolve_account_count_limits,
    should_enforce_message_limit,
)
from core_engine.services.campaign_capacity import (
    AccountCapacityInput,
    immediate_slots_for_account,
    today_slots_for_account,
)
from core_engine.services.rubika_quota import reserve_send_quota
from workers.rate_limit import (
    hourly_send_count,
    is_hourly_cap_reached,
    record_successful_send,
)

IRAN = ZoneInfo("Asia/Tehran")


class FakeRedis:
    """In-process Redis for quota tests. Never talks to a live instance."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key):
        value = self.data.get(str(key))
        return None if value is None else value.encode()

    async def set(self, key, value, ex=None):
        self.data[str(key)] = value.decode() if isinstance(value, bytes) else str(value)
        return True

    async def incr(self, key):
        key_s = str(key)
        n = int(self.data.get(key_s) or 0) + 1
        self.data[key_s] = str(n)
        return n

    async def expire(self, key, ttl):
        return True

    async def ttl(self, key):
        return -1

    async def exists(self, key):
        return 1 if str(key) in self.data else 0

    async def delete(self, *keys):
        for key in keys:
            self.data.pop(str(key), None)
        return len(keys)

    async def eval(self, script, numkeys, *args):
        keys = [str(item) for item in args[:numkeys]]
        argv = list(args[numkeys:])
        if "DAILY_CAP_REACHED" in script:
            return self._reserve(keys, argv)
        if "DECR" in script:
            return self._release(keys)
        raise NotImplementedError(script[:80])

    def _reserve(self, keys, argv):
        daily_key, hourly_key, delay_key, reserve_key = keys
        daily_cap, hourly_cap = int(argv[0]), int(argv[1])
        if int(self.data.get(delay_key) or 0) or False:
            pass
        daily = int(self.data.get(daily_key) or 0)
        hourly = int(self.data.get(hourly_key) or 0)
        if daily_cap > 0 and daily >= daily_cap:
            return ["deny", "DAILY_CAP_REACHED", "0", str(daily), str(hourly)]
        if hourly_cap > 0 and hourly >= hourly_cap:
            return ["deny", "HOURLY_CAP_REACHED", "0", str(daily), str(hourly)]
        daily += 1
        hourly += 1
        self.data[daily_key] = str(daily)
        self.data[hourly_key] = str(hourly)
        self.data[reserve_key] = "1"
        return ["ok", "RESERVED", "0", str(daily), str(hourly)]

    def _release(self, keys):
        daily_key, hourly_key, reserve_key = keys[:3]
        if reserve_key not in self.data:
            return 0
        self.data.pop(reserve_key, None)
        daily = int(self.data.get(daily_key) or 0)
        hourly = int(self.data.get(hourly_key) or 0)
        if daily > 0:
            self.data[daily_key] = str(daily - 1)
        if hourly > 0:
            self.data[hourly_key] = str(hourly - 1)
        return 1


def test_null_means_unlimited_and_rejects_invalid():
    assert normalize_account_message_limit(None, field="hourly") is None
    assert normalize_account_message_limit(10, field="hourly") == 10
    assert is_unlimited_message_limit(None) is True
    assert should_enforce_message_limit(None) is False
    assert should_enforce_message_limit(5) is True
    assert remaining_for_limit(None, 99) is None
    assert remaining_for_limit(10, 3) == 7
    assert redis_cap_arg(None) == UNLIMITED_REDIS_SENTINEL
    assert redis_cap_arg(8) == 8
    with pytest.raises(InvalidAccountMessageLimit):
        normalize_account_message_limit(0, field="hourly")
    with pytest.raises(InvalidAccountMessageLimit):
        normalize_account_message_limit(-1, field="daily")
    with pytest.raises(InvalidAccountMessageLimit):
        normalize_account_message_limit(1.5, field="hourly")
    with pytest.raises(InvalidAccountMessageLimit):
        normalize_account_message_limit("10", field="hourly")
    with pytest.raises(InvalidAccountMessageLimit):
        normalize_account_message_limit(True, field="hourly")


def test_env_and_lifecycle_are_not_count_fallbacks():
    account = type("A", (), {})()
    account.hourly_message_limit = None
    account.daily_message_limit = None
    hourly, daily = resolve_account_count_limits(account)
    assert hourly is None and daily is None
    account.hourly_message_limit = 10
    hourly, daily = resolve_account_count_limits(account)
    assert hourly == 10 and daily is None


@pytest.mark.asyncio
async def test_unlimited_reserve_still_increments_counters():
    redis = FakeRedis()
    first = await reserve_send_quota(redis, 42, daily_cap=None, hourly_cap=None)
    assert first.ok
    assert first.sent_today == 1
    assert first.sent_this_hour == 1
    second = await reserve_send_quota(redis, 42, daily_cap=None, hourly_cap=None)
    assert second.ok
    assert second.sent_today == 2


@pytest.mark.asyncio
async def test_hourly_limited_daily_unlimited():
    redis = FakeRedis()
    ok = await reserve_send_quota(redis, 7, daily_cap=None, hourly_cap=1)
    assert ok.ok
    denied = await reserve_send_quota(redis, 7, daily_cap=None, hourly_cap=1)
    assert denied.ok is False
    assert denied.code == "HOURLY_CAP_REACHED"
    assert denied.sent_today == 1


@pytest.mark.asyncio
async def test_hourly_unlimited_daily_limited():
    redis = FakeRedis()
    ok = await reserve_send_quota(redis, 8, daily_cap=1, hourly_cap=None)
    assert ok.ok
    denied = await reserve_send_quota(redis, 8, daily_cap=1, hourly_cap=None)
    assert denied.ok is False
    assert denied.code == "DAILY_CAP_REACHED"


@pytest.mark.asyncio
async def test_both_unlimited():
    redis = FakeRedis()
    for _ in range(5):
        result = await reserve_send_quota(redis, 9, daily_cap=None, hourly_cap=None)
        assert result.ok
    assert result.sent_today == 5


@pytest.mark.asyncio
async def test_setting_limit_mid_window_counts_previous_usage():
    redis = FakeRedis()
    await reserve_send_quota(redis, 11, daily_cap=None, hourly_cap=None)
    await reserve_send_quota(redis, 11, daily_cap=None, hourly_cap=None)
    denied = await reserve_send_quota(redis, 11, daily_cap=2, hourly_cap=None)
    assert denied.ok is False
    assert denied.code == "DAILY_CAP_REACHED"
    assert denied.sent_today == 2


@pytest.mark.asyncio
async def test_whatsapp_unlimited_still_increments_hourly_counter():
    redis = FakeRedis()
    assert await is_hourly_cap_reached(redis, 3, 0) is False
    count = await record_successful_send(redis, 3)
    assert count == 1
    assert await hourly_send_count(redis, 3) == 1
    assert await is_hourly_cap_reached(redis, 3, 0) is False
    assert await is_hourly_cap_reached(redis, 3, 1) is True


def test_capacity_unlimited_vs_unknown():
    now = datetime(2026, 8, 17, 10, 0, tzinfo=IRAN)
    unlimited = AccountCapacityInput(
        account_id=1,
        assigned_remaining=40,
        remaining_daily=None,
        remaining_hourly=None,
        daily_cap=None,
        hourly_cap=None,
        daily_unlimited=True,
        hourly_unlimited=True,
        quota_known=True,
        window_open=True,
        applies_quota=True,
        min_interval_seconds=0,
    )
    assert immediate_slots_for_account(unlimited) == 40
    unknown_limited = AccountCapacityInput(
        account_id=2,
        assigned_remaining=40,
        remaining_daily=None,
        remaining_hourly=None,
        daily_cap=10,
        hourly_cap=5,
        daily_unlimited=False,
        hourly_unlimited=False,
        quota_known=False,
        window_open=True,
        applies_quota=True,
    )
    assert immediate_slots_for_account(unknown_limited) == 0
    assert today_slots_for_account(unknown_limited, windows=(), now=now) is None
    mixed = AccountCapacityInput(
        account_id=3,
        assigned_remaining=40,
        remaining_daily=None,
        remaining_hourly=4,
        daily_cap=None,
        hourly_cap=10,
        daily_unlimited=True,
        hourly_unlimited=False,
        quota_known=True,
        window_open=True,
        applies_quota=True,
        min_interval_seconds=0,
    )
    assert immediate_slots_for_account(mixed) == 4
