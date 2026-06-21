import json
import os

import pytest

from workers.bale_worker import BaleWorker
from workers.config import get_worker_settings


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, list[str]] = {}

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def get(self, key: str) -> str | None:
        return None

    async def lpop(self, key: str) -> str | None:
        items = self.store.get(key) or []
        if not items:
            return None
        return items.pop(0)

    async def lpush(self, key: str, value: str) -> int:
        self.store.setdefault(key, []).insert(0, value)
        return len(self.store[key])

    async def rpush(self, key: str, value: str) -> int:
        self.store.setdefault(key, []).append(value)
        return len(self.store[key])


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(
        "workers.base_worker.Redis.from_url",
        lambda *args, **kwargs: fake,
    )
    return fake


@pytest.fixture
def dry_run_settings(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    get_worker_settings.cache_clear()
    yield
    get_worker_settings.cache_clear()


@pytest.mark.asyncio
async def test_bale_worker_processes_staged_payload_dry_run(
    fake_redis,
    dry_run_settings,
    pg_session_factory,
    recipient_bundle,
):
    from core_engine.models import CampaignRecipient, SendStatus

    campaign_id, contact_id, _session = recipient_bundle
    database_url = os.environ["DATABASE_URL"]

    payload = {
        "campaign_id": campaign_id,
        "contact_id": contact_id,
        "channel": "bale",
        "final_text": "worker hello",
        "phone": "+989120000099",
        "account_id": 1,
    }
    queue_key = "queue:bale:1"
    fake_redis.store[queue_key] = [json.dumps(payload, ensure_ascii=False)]

    worker = BaleWorker(
        account_id=1,
        redis_url="redis://localhost:6379/0",
        database_url=database_url,
        poll_interval_seconds=1,
    )
    await worker.run_once()

    check_session = pg_session_factory()
    try:
        recipient = (
            check_session.query(CampaignRecipient)
            .filter(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.contact_id == contact_id,
            )
            .first()
        )
        assert recipient is not None
        assert recipient.send_status == SendStatus.DRY_RUN
    finally:
        check_session.close()
