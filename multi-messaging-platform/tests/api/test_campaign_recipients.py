from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from core_engine.api.auth import get_current_user
from core_engine.main import app
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    Message,
    PlatformType,
    RenderStatus,
    SendStatus,
)
from core_engine.services.campaign_recipients import recipient_to_response

client = TestClient(app)


@pytest.fixture
def campaign_with_mixed_recipients(pg_session_factory):
    s = pg_session_factory()
    campaign = Campaign(
        name="Recipients Test",
        title="Recipients Test",
        channel="bale",
        platform=PlatformType.BALE,
        status=CampaignStatus.RUNNING.value,
        template_text="Hi",
    )
    s.add(campaign)
    s.flush()
    account = Account(
        platform=PlatformType.BALE,
        status=AccountStatus.ACTIVE,
        label="Sender One",
        phone_number="09120000001",
    )
    s.add(account)
    s.flush()

    statuses = [SendStatus.PENDING, SendStatus.DELIVERED, SendStatus.FAILED_PERMANENT]
    contacts = []
    for i, status in enumerate(statuses):
        contact = Contact(phone=f"0912987654{i}", first_name=f"User{i}")
        s.add(contact)
        s.flush()
        contacts.append(contact)
        recipient = CampaignRecipient(
            campaign_id=campaign.id,
            contact_id=contact.id,
            send_status=status,
        )
        s.add(recipient)
        s.flush()
        if i == 1:
            message = Message(
                campaign_id=campaign.id,
                account_id=account.id,
                contact_id=contact.id,
                rendered_text="Hi",
                dedupe_key=f"recipient-report-{campaign.id}-{contact.id}",
            )
            s.add(message)
            s.flush()
            recipient.final_message_id = message.id

    s.commit()
    yield campaign, contacts, s

    s.query(CampaignRecipient).filter(CampaignRecipient.campaign_id == campaign.id).delete()
    s.query(Message).filter(Message.campaign_id == campaign.id).delete()
    for contact in contacts:
        s.query(Contact).filter(Contact.id == contact.id).delete()
    s.query(Campaign).filter(Campaign.id == campaign.id).delete()
    s.query(Account).filter(Account.id == account.id).delete()
    s.commit()
    s.close()


def test_list_campaign_recipients(client, campaign_with_mixed_recipients):
    campaign, contacts, _ = campaign_with_mixed_recipients

    response = client.get(
        f"/campaigns/{campaign.id}/recipients",
        headers={"Authorization": "Bearer fake_token"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["campaign_id"] == campaign.id
    assert data["total_count"] == 3
    assert len(data["items"]) == 3
    assert data["items"][0]["phone"].startswith("0912")
    assert data["items"][0]["first_name"] == "User0"
    assert data["items"][0]["final_message_id"] is None
    assert data["items"][0]["account_id"] is None
    assert data["items"][0]["sender_account"] is None

    sent_item = data["items"][1]
    assert sent_item["final_message_id"] is not None
    assert sent_item["account_id"] == sent_item["sender_account"]["account_id"]
    assert sent_item["sender_account"] == {
        "account_id": sent_item["account_id"],
        "label": "Sender One",
        "account_identifier": "09120000001",
        "platform": "bale",
        "status": "active",
    }


def test_recipient_response_keeps_account_id_when_account_relation_is_missing():
    contact = Contact(id=101, phone="09121111111")
    message = Message(
        id=202,
        campaign_id=1,
        contact_id=101,
        account_id=303,
        dedupe_key="legacy-missing-account",
    )
    recipient = CampaignRecipient(
        id=404,
        campaign_id=1,
        contact_id=101,
        final_message_id=202,
        render_status=RenderStatus.PENDING,
        send_status=SendStatus.PENDING,
        updated_at=datetime.utcnow(),
    )
    recipient.final_message = message

    item = recipient_to_response(recipient, contact)

    assert item.account_id == 303
    assert item.sender_account is None


def test_list_campaign_recipients_query_count_is_constant(
    client, campaign_with_mixed_recipients
):
    campaign, _, session = campaign_with_mixed_recipients
    campaign_id = campaign.id
    select_count = 0

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal select_count
        if statement.lstrip().upper().startswith("SELECT"):
            select_count += 1

    event.listen(session.bind, "before_cursor_execute", count_selects)
    try:
        response = client.get(
            f"/campaigns/{campaign_id}/recipients",
            headers={"Authorization": "Bearer fake_token"},
        )
    finally:
        event.remove(session.bind, "before_cursor_execute", count_selects)

    assert response.status_code == 200
    assert select_count == 3


def test_list_campaign_recipients_filter_by_send_status(client, campaign_with_mixed_recipients):
    campaign, _, _ = campaign_with_mixed_recipients

    response = client.get(
        f"/campaigns/{campaign.id}/recipients?send_status=delivered",
        headers={"Authorization": "Bearer fake_token"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["send_status"] == "delivered"


def test_list_campaign_recipients_pagination(client, campaign_with_mixed_recipients):
    campaign, _, _ = campaign_with_mixed_recipients

    response = client.get(
        f"/campaigns/{campaign.id}/recipients?limit=2&offset=1",
        headers={"Authorization": "Bearer fake_token"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 3
    assert len(data["items"]) == 2
    assert data["limit"] == 2
    assert data["offset"] == 1


def test_list_campaign_recipients_not_found(client):
    response = client.get(
        "/campaigns/999999/recipients",
        headers={"Authorization": "Bearer fake_token"},
    )
    assert response.status_code == 404


def test_list_campaign_recipients_invalid_send_status(client, campaign_with_mixed_recipients):
    campaign, _, _ = campaign_with_mixed_recipients

    response = client.get(
        f"/campaigns/{campaign.id}/recipients?send_status=not_a_status",
        headers={"Authorization": "Bearer fake_token"},
    )
    assert response.status_code == 400


def test_export_campaign_recipients_csv(client, campaign_with_mixed_recipients):
    campaign, _, _ = campaign_with_mixed_recipients

    response = client.get(
        f"/campaigns/{campaign.id}/recipients/export",
        headers={"Authorization": "Bearer fake_token"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers.get("content-disposition", "")

    text = response.content.decode("utf-8-sig")
    lines = [line for line in text.strip().splitlines() if line]
    assert lines[0].startswith("id,campaign_id,contact_id,phone")
    assert len(lines) == 4  # header + 3 rows


def test_export_campaign_recipients_csv_filter(client, campaign_with_mixed_recipients):
    campaign, _, _ = campaign_with_mixed_recipients

    response = client.get(
        f"/campaigns/{campaign.id}/recipients/export?send_status=delivered",
        headers={"Authorization": "Bearer fake_token"},
    )

    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    lines = [line for line in text.strip().splitlines() if line]
    assert len(lines) == 2
    assert "delivered" in lines[1]


def test_export_campaign_recipients_not_found(client):
    response = client.get(
        "/campaigns/999999/recipients/export",
        headers={"Authorization": "Bearer fake_token"},
    )
    assert response.status_code == 404


def test_viewer_can_list_campaign_recipients(client, campaign_with_mixed_recipients):
    campaign, _, _ = campaign_with_mixed_recipients

    async def _viewer():
        return {"username": "viewer", "password": "viewer123", "role": "viewer"}

    app.dependency_overrides[get_current_user] = _viewer
    try:
        response = client.get(
            f"/campaigns/{campaign.id}/recipients",
            headers={"Authorization": "Bearer fake_token"},
        )
        assert response.status_code == 200
        assert response.json()["total_count"] == 3
    finally:
        app.dependency_overrides.pop(get_current_user, None)
