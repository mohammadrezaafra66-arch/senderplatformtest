import pytest

from core_engine.tasks import add_numbers


@pytest.mark.integration
def test_add_numbers_task_eager(celery_eager):
    result = add_numbers.delay(2, 3).get()
    assert result == 5
