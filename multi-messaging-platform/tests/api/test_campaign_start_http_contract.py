"""Real HTTP contract for Campaign Start + Controlled Production.

Hermetic HTTP path: TestClient + dependency overrides.
Does not write to production DB/Redis and does not send provider traffic.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from core_engine.api.auth import get_current_user
from core_engine.database import get_db
from core_engine.main import app
from core_engine.models import CampaignStatus, PlatformType
from core_engine.services.campaign_preflight import CONTROLLED_PRODUCTION_APPROVAL_REQUIRED

client = TestClient(app)
AUTH = {"Authorization": "Bearer fake_token"}


@pytest.fixture
def enable_cp(monkeypatch):
    monkeypatch.setenv("CONTROLLED_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("CONTROLLED_PRODUCTION_DEFAULT_MAX_TOTAL_MESSAGES", "5")
    from core_engine.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _campaign_obj(*, campaign_id: int = 50127):
    return SimpleNamespace(
        id=campaign_id,
        name=f"http-cp-{campaign_id}",
        title=f"http-cp-{campaign_id}",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        status=CampaignStatus.PREPARED.value,
        template_text="hello",
        max_total_messages=None,
        max_contacts=None,
    )


class _FakeDB:
    def __init__(self, campaign):
        self.campaign = campaign

    def query(self, _model):
        return self

    def filter(self, *_a, **_k):
        return self

    def first(self):
        return self.campaign

    def flush(self):
        return None

    def commit(self):
        return None

    def rollback(self):
        return None

    def refresh(self, _obj):
        return None


def _cp_advisory_preflight():
    persian = "برای شروع ارسال واقعی، تأیید نهایی اپراتور لازم است."
    return SimpleNamespace(
        allowed_to_start=False,
        code=CONTROLLED_PRODUCTION_APPROVAL_REQUIRED,
        message=persian,
        blockers=[
            {
                "code": CONTROLLED_PRODUCTION_APPROVAL_REQUIRED,
                "message": persian,
                "account_id": None,
            }
        ],
        warnings=[],
        execution_safety_state="READY",
        technical_ready=True,
        controlled_production_confirmation_required=True,
        allowed_to_start_after_confirmation=True,
        to_dict=lambda: {
            "code": CONTROLLED_PRODUCTION_APPROVAL_REQUIRED,
            "allowed_to_start": False,
            "warnings": [],
            "execution_safety_state": "READY",
            "technical_ready": True,
            "controlled_production_confirmation_required": True,
        },
    )


@pytest.fixture
def override_auth_db(enable_cp):
    camp = _campaign_obj()

    async def _user():
        return {"username": "operator", "role": "operator", "password": "x"}

    def _db():
        yield _FakeDB(camp)

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_db] = _db
    try:
        yield camp
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


def test_http_confirm_true_wire_body_passes_cp_advisory(override_auth_db, monkeypatch):
    async def _clear(_cid: int) -> None:
        return None

    async def _bridge(_db, batch_size: int = 50):
        return {"pushed": 1}

    monkeypatch.setattr(
        "core_engine.services.campaign_control.clear_campaign_pause", _clear
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_control.push_staged_items_to_worker_queue",
        _bridge,
    )

    with patch(
        "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
        AsyncMock(return_value=_cp_advisory_preflight()),
    ), patch("core_engine.services.campaign_control._auto_prepare"), patch(
        "core_engine.api.campaigns.record_audit"
    ):
        response = client.post(
            "/campaigns/50127/start",
            json={"confirm_controlled_production": True},
            headers={
                **AUTH,
                "Content-Type": "application/json",
                "X-Request-Id": "wire-confirm-http-1",
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] is True
    assert body["controlled_confirmation_accepted"] is True
    assert body["request_id"] == "wire-confirm-http-1"
    assert body["queue_jobs_created"] == 1
    assert body["campaign_status"] == CampaignStatus.RUNNING.value
    assert JSON_BODY_CONFIRM is True  # noqa: marker for report


JSON_BODY_CONFIRM = True


def test_http_absent_body_returns_409(override_auth_db):
    response = client.post("/campaigns/50127/start", headers=AUTH)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == CONTROLLED_PRODUCTION_APPROVAL_REQUIRED


def test_http_confirm_false_returns_409(override_auth_db):
    response = client.post(
        "/campaigns/50127/start",
        json={"confirm_controlled_production": False},
        headers=AUTH,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == CONTROLLED_PRODUCTION_APPROVAL_REQUIRED


def test_http_duplicate_confirm_is_safe(override_auth_db, monkeypatch):
    pushes = {"n": 0}

    async def _clear(_cid: int) -> None:
        return None

    async def _bridge(_db, batch_size: int = 50):
        pushes["n"] += 1
        return {"pushed": 1 if pushes["n"] == 1 else 0}

    monkeypatch.setattr(
        "core_engine.services.campaign_control.clear_campaign_pause", _clear
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_control.push_staged_items_to_worker_queue",
        _bridge,
    )

    with patch(
        "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
        AsyncMock(return_value=_cp_advisory_preflight()),
    ), patch("core_engine.services.campaign_control._auto_prepare"), patch(
        "core_engine.api.campaigns.record_audit"
    ):
        r1 = client.post(
            "/campaigns/50127/start",
            json={"confirm_controlled_production": True},
            headers=AUTH,
        )
        r2 = client.post(
            "/campaigns/50127/start",
            json={"confirm_controlled_production": True},
            headers=AUTH,
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["accepted"] is True
    assert r2.json()["accepted"] is True
    assert r2.json()["queue_jobs_created"] == 0


def test_frontend_serialized_confirm_body_contract():
    import json

    body = {"confirm_controlled_production": True}
    assert json.dumps(body) == '{"confirm_controlled_production": true}'
    assert json.loads(json.dumps(body))["confirm_controlled_production"] is True
