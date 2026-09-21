"""Name-level pytest database guard — no schema/row writes."""

from __future__ import annotations

import pytest

from tests.isolation import (
    DEFAULT_PYTEST_DATABASE,
    REFUSING_NON_TEST_DATABASE,
    assert_pytest_database_name,
    assert_pytest_database_url,
)


@pytest.mark.parametrize(
    "name",
    ["mmp_isolated_readiness", "mmp_db", "postgres", "template1", ""],
)
def test_forbidden_database_names_are_refused(name: str):
    with pytest.raises(RuntimeError, match=REFUSING_NON_TEST_DATABASE):
        assert_pytest_database_name(name)


def test_pytest_prefix_database_is_allowed():
    assert assert_pytest_database_name(DEFAULT_PYTEST_DATABASE) == DEFAULT_PYTEST_DATABASE


def test_url_helpers_match_name_policy():
    with pytest.raises(RuntimeError, match=REFUSING_NON_TEST_DATABASE):
        assert_pytest_database_url("postgresql://u:p@127.0.0.1:5435/mmp_isolated_readiness")
    with pytest.raises(RuntimeError, match=REFUSING_NON_TEST_DATABASE):
        assert_pytest_database_url("postgresql://u:p@127.0.0.1:5432/mmp_db")
    assert (
        assert_pytest_database_url(
            f"postgresql://u:p@127.0.0.1:5435/{DEFAULT_PYTEST_DATABASE}"
        )
        == DEFAULT_PYTEST_DATABASE
    )
