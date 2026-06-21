import pytest
from fastapi.testclient import TestClient

from core_engine.main import app
from core_engine.models import Campaign, CampaignStatus, PlatformType
from workers.redis_keys import campaign_pause_key

client = TestClient(app)


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(
        "core_engine.services.campaign_control.get_redis_client",
        lambda: fake,
    )

    async def _ping_ok() -> bool:
        return True

    monkeypatch.setattr(
        "core_engine.services.campaign_control.ping_redis",
        _ping_ok,
    )
    return fake


@pytest.fixture
def draft_campaign(pg_session_factory):
    s = pg_session_factory()
    campaign = Campaign(
        name="Start Stop Test",
        title="Start Stop Test",
        channel="bale",
        platform=PlatformType.BALE,
        status=CampaignStatus.DRAFT.value,
        template_text="Hello",
    )
    s.add(campaign)
    s.commit()
    s.refresh(campaign)
    yield campaign, s
    s.query(Campaign).filter(Campaign.id == campaign.id).delete()
    s.commit()
    s.close()


@pytest.fixture
def running_campaign(pg_session_factory):
    s = pg_session_factory()
    campaign = Campaign(
        name="Running Campaign",
        title="Running Campaign",
        channel="bale",
        platform=PlatformType.BALE,
        status=CampaignStatus.RUNNING.value,
        template_text="Hello",
    )
    s.add(campaign)
    s.commit()
    s.refresh(campaign)
    yield campaign, s
    s.query(Campaign).filter(Campaign.id == campaign.id).delete()
    s.commit()
    s.close()


@pytest.fixture
def mock_bridge(monkeypatch):
    async def _fake_bridge(db, batch_size: int = 500):
        return {"pushed": 0, "skipped": 0, "blocked": 0, "failed": 0}

    monkeypatch.setattr(
        "core_engine.services.campaign_control.push_staged_items_to_worker_queue",
        _fake_bridge,
    )


def test_start_campaign_sets_running_and_clears_pause(
    fake_redis,
    mock_bridge,
    draft_campaign,
):
    campaign, session = draft_campaign
    pause_key = campaign_pause_key(campaign.id)
    fake_redis.store[pause_key] = "true"

    response = client.post(
        f"/campaigns/{campaign.id}/start",
        headers={"Authorization": "Bearer fake_token"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "running"
    assert data["campaign_id"] == campaign.id
    assert pause_key not in fake_redis.store

    session.expire_all()
    updated = session.query(Campaign).filter(Campaign.id == campaign.id).first()
    assert updated.status == CampaignStatus.RUNNING.value


def test_stop_campaign_sets_paused_and_redis_flag(fake_redis, running_campaign):
    campaign, session = running_campaign

    response = client.post(
        f"/campaigns/{campaign.id}/stop",
        headers={"Authorization": "Bearer fake_token"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "paused"
    assert data["paused_in_redis"] is True

    pause_key = campaign_pause_key(campaign.id)
    assert fake_redis.store.get(pause_key) == "true"

    session.expire_all()
    updated = session.query(Campaign).filter(Campaign.id == campaign.id).first()
    assert updated.status == CampaignStatus.PAUSED.value


def test_start_campaign_not_found(fake_redis, mock_bridge):
    response = client.post(
        "/campaigns/999999/start",
        headers={"Authorization": "Bearer fake_token"},
    )
    assert response.status_code == 404


def test_stop_campaign_not_found(fake_redis):
    response = client.post(
        "/campaigns/999999/stop",
        headers={"Authorization": "Bearer fake_token"},
    )
    assert response.status_code == 404


def test_start_campaign_invalid_status(fake_redis, mock_bridge, pg_session_factory):
    s = pg_session_factory()
    campaign = Campaign(
        name="Completed",
        title="Completed",
        channel="bale",
        platform=PlatformType.BALE,
        status=CampaignStatus.COMPLETED.value,
        template_text="Hello",
    )
    s.add(campaign)
    s.commit()
    s.refresh(campaign)

    response = client.post(
        f"/campaigns/{campaign.id}/start",
        headers={"Authorization": "Bearer fake_token"},
    )
    assert response.status_code == 400

    s.query(Campaign).filter(Campaign.id == campaign.id).delete()
    s.commit()
    s.close()
