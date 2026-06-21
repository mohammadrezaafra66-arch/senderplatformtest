import os

import pytest

from workers.account_pool import (
    parse_account_id_list,
    resolve_assigned_account_ids,
    resolve_pool_index,
    shard_account_ids,
)


def test_parse_account_id_list_dedupes_and_sorts():
    assert parse_account_id_list("3,1,2,1") == [1, 2, 3]


def test_parse_account_id_list_empty():
    assert parse_account_id_list("") == []
    assert parse_account_id_list(" , ") == []


def test_parse_account_id_list_rejects_invalid():
    with pytest.raises(ValueError, match="Invalid account id"):
        parse_account_id_list("1,abc")


def test_shard_account_ids_modulo():
    account_ids = [1, 2, 3, 4, 5]
    assert shard_account_ids(account_ids, pool_size=2, pool_index=0) == [2, 4]
    assert shard_account_ids(account_ids, pool_size=2, pool_index=1) == [1, 3, 5]


def test_account_belongs_to_shard():
    from workers.account_pool import account_belongs_to_shard

    assert account_belongs_to_shard(7, pool_size=3, pool_index=1) is True
    assert account_belongs_to_shard(8, pool_size=3, pool_index=1) is False


def test_shard_account_ids_rejects_bad_index():
    with pytest.raises(ValueError, match="pool_index"):
        shard_account_ids([1, 2], pool_size=2, pool_index=2)


def test_resolve_pool_index_explicit():
    assert resolve_pool_index(pool_size=3, explicit_index=5) == 2


def test_resolve_pool_index_from_hostname(monkeypatch):
    monkeypatch.setenv("HOSTNAME", "whatsapp-worker-a")
    first = resolve_pool_index(pool_size=4, explicit_index=-1)
    second = resolve_pool_index(pool_size=4, explicit_index=-1)
    assert first == second
    assert 0 <= first < 4


def test_resolve_assigned_account_ids_fallback_to_worker_account_id():
    assigned = resolve_assigned_account_ids(
        account_ids_raw="",
        pool_size=1,
        pool_index=0,
        fallback_account_id=7,
    )
    assert assigned == [7]


def test_resolve_assigned_account_ids_shards_list():
    assigned = resolve_assigned_account_ids(
        account_ids_raw="1,2,3,4,5",
        pool_size=2,
        pool_index=1,
        fallback_account_id=1,
    )
    assert assigned == [1, 3, 5]


def test_resolve_assigned_account_ids_empty_shard_raises():
    with pytest.raises(ValueError, match="No accounts assigned"):
        resolve_assigned_account_ids(
            account_ids_raw="1,2",
            pool_size=3,
            pool_index=0,
            fallback_account_id=1,
        )
