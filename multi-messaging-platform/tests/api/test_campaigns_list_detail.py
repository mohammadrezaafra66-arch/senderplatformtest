import pytest
from fastapi.testclient import TestClient

from core_engine.main import app
from core_engine.models import Campaign, CampaignRecipient, CampaignStatus, Contact, PlatformType

client = TestClient(app)


@pytest.fixture
def campaign_with_recipients(pg_session_factory):
    """یک کمپین با recipients برای تست."""
    s = pg_session_factory()
    campaign = Campaign(
        name="Test Campaign",
        title="Test Campaign",
        channel="bale",
        platform=PlatformType.BALE,
        status=CampaignStatus.RUNNING.value,
        template_text="Hello {{first_name}}",
    )
    s.add(campaign)
    s.flush()

    # Add 3 recipients
    for i in range(3):
        contact = Contact(phone=f"09121234567{i}", consent_status="consent")
        s.add(contact)
        s.flush()
        recipient = CampaignRecipient(campaign_id=campaign.id, contact_id=contact.id)
        s.add(recipient)

    s.commit()
    yield campaign, s
    s.query(CampaignRecipient).filter(CampaignRecipient.campaign_id == campaign.id).delete()
    for i in range(3):
        s.query(Contact).filter(Contact.phone == f"09121234567{i}").delete()
    s.query(Campaign).filter(Campaign.id == campaign.id).delete()
    s.commit()
    s.close()


def test_list_campaigns_empty(client, pg_session_factory):
    """لیست خالی است اگر کمپین نباشد."""
    response = client.get(
        "/campaigns?status=__pytest_nonexistent__",
        headers={"Authorization": "Bearer fake_token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total_count"] == 0


def test_list_campaigns_with_pagination(client, campaign_with_recipients):
    """لیست کمپین‌ها با pagination."""
    campaign, _ = campaign_with_recipients
    response = client.get("/campaigns?limit=10&offset=0", headers={"Authorization": "Bearer fake_token"})
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] >= 1
    assert len(data["items"]) >= 1
    assert data["items"][0]["id"] == campaign.id
    assert data["items"][0]["total_recipients"] == 3


def test_get_campaign_detail(client, campaign_with_recipients):
    """جزئیات کمپین با stats."""
    campaign, _ = campaign_with_recipients
    response = client.get(f"/campaigns/{campaign.id}", headers={"Authorization": "Bearer fake_token"})
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == campaign.id
    assert data["title"] == "Test Campaign"
    assert data["stats"]["total_recipients"] == 3


def test_get_campaign_detail_not_found(client):
    """کمپین‌های inexistent 404 برمی‌گرداند."""
    response = client.get("/campaigns/99999", headers={"Authorization": "Bearer fake_token"})
    assert response.status_code == 404


def test_list_campaigns_filter_by_status(client, campaign_with_recipients):
    """فیلتر کمپین‌ها بر اساس status."""
    response = client.get("/campaigns?status=running", headers={"Authorization": "Bearer fake_token"})
    assert response.status_code == 200
    data = response.json()
    # تمام items باید RUNNING باشند
    for item in data["items"]:
        assert item["status"] == "running"


def test_list_campaigns_filter_by_platform(client, campaign_with_recipients):
    """فیلتر کمپین‌ها بر اساس platform."""
    response = client.get("/campaigns?platform=bale", headers={"Authorization": "Bearer fake_token"})
    assert response.status_code == 200
    data = response.json()
    for item in data["items"]:
        assert item["platform"] == "bale"
