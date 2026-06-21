import pytest

from core_engine.services.worker_pool_status import account_covered_by_pool


def test_account_covered_by_pool():
    workers = [
        {"hostname": "a", "assigned_account_ids": [1, 3]},
        {"hostname": "b", "assigned_account_ids": [2, 4]},
    ]
    assert account_covered_by_pool(3, workers) is True
    assert account_covered_by_pool(9, workers) is False
