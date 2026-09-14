"""Global contact directory API tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from core_engine.api.auth import get_current_user
from core_engine.main import app
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    ConsentStatus,
    Contact,
    ImportBatch,
    ImportRow,
    ImportRowStatus,
    ImportStatus,
    PlatformType,
)

client = TestClient(app)
AUTH = {"Authorization": "Bearer fake_token"}


def _contact(
    session,
    *,
    phone_suffix: str | None = None,
    first_name: str = "آزمایش",
    full_name: str | None = None,
    created_at: datetime | None = None,
    source_import_id: int | None = None,
    consent_status: str = ConsentStatus.ALLOWED.value,
    blacklisted: bool = False,
) -> Contact:
    suffix = phone_suffix or uuid.uuid4().hex[:8]
    contact = Contact(
        first_name=first_name,
        full_name=full_name,
        phone=f"+9891{suffix[:8]}",
        phone_e164=f"+9891{suffix[:8]}",
        consent_status=consent_status,
        blacklisted=blacklisted,
        source_import_id=source_import_id,
    )
    if created_at is not None:
        contact.created_at = created_at
        contact.updated_at = created_at
    session.add(contact)
    session.flush()
    return contact


def _import_batch(session, *, file_name: str = "contacts.xlsx") -> ImportBatch:
    batch = ImportBatch(
        file_name=file_name,
        original_file_name=file_name,
        status=ImportStatus.COMMITTED,
        row_count=1,
        valid_rows_count=1,
    )
    session.add(batch)
    session.flush()
    return batch


def _campaign(session) -> Campaign:
    campaign = Campaign(
        name=f"dir-{uuid.uuid4().hex[:8]}",
        title=f"dir-{uuid.uuid4().hex[:8]}",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        status=CampaignStatus.DRAFT.value,
        template_text="hi",
    )
    session.add(campaign)
    session.flush()
    return campaign


def test_list_contacts_returns_all_without_search(pg_session_factory):
    session = pg_session_factory()
    old = _contact(session, first_name="قدیمی", created_at=datetime.utcnow() - timedelta(days=30))
    new = _contact(session, first_name="جدید", created_at=datetime.utcnow())
    session.commit()

    response = client.get("/contacts", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    ids = {item["contact_id"] for item in body["items"]}
    assert old.id in ids
    assert new.id in ids
    assert body["total_count"] >= 2


def test_list_contacts_includes_contact_without_campaign(pg_session_factory):
    session = pg_session_factory()
    orphan = _contact(session, first_name="بدون-کمپین")
    session.commit()

    response = client.get("/contacts", headers=AUTH)
    assert response.status_code == 200
    ids = {item["contact_id"] for item in response.json()["items"]}
    assert orphan.id in ids


def test_list_contacts_includes_archived_campaign_contact(pg_session_factory):
    session = pg_session_factory()
    contact = _contact(session, first_name="آرشیو-کمپین")
    campaign = _campaign(session)
    campaign.archived_at = datetime.utcnow()
    session.add(
        CampaignRecipient(
            campaign_id=campaign.id,
            contact_id=contact.id,
            phone=contact.phone,
        )
    )
    session.commit()

    response = client.get("/contacts", headers=AUTH)
    assert response.status_code == 200
    ids = {item["contact_id"] for item in response.json()["items"]}
    assert contact.id in ids


def test_list_contacts_includes_contact_with_archived_account_campaign(pg_session_factory):
    session = pg_session_factory()
    contact = _contact(session, first_name="اکانت-آرشیو")
    account = Account(
        platform=PlatformType.RUBIKA,
        status=AccountStatus.ACTIVE,
        label="archived-acct",
        phone_number=f"09{uuid.uuid4().int % 10**9:09d}",
        archived_at=datetime.utcnow(),
    )
    session.add(account)
    session.flush()
    campaign = _campaign(session)
    session.add(
        CampaignRecipient(
            campaign_id=campaign.id,
            contact_id=contact.id,
            phone=contact.phone,
        )
    )
    session.commit()

    response = client.get("/contacts", headers=AUTH)
    assert response.status_code == 200
    ids = {item["contact_id"] for item in response.json()["items"]}
    assert contact.id in ids


def test_list_contacts_pagination_and_total_count(pg_session_factory):
    session = pg_session_factory()
    created = [_contact(session, phone_suffix=f"{i:08d}") for i in range(3)]
    session.commit()

    response = client.get("/contacts?limit=1&offset=0", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 1
    assert body["offset"] == 0
    assert len(body["items"]) == 1
    assert body["total_count"] >= len(created)

    page2 = client.get("/contacts?limit=1&offset=1", headers=AUTH)
    assert page2.status_code == 200
    assert page2.json()["items"][0]["contact_id"] != body["items"][0]["contact_id"]


def test_list_contacts_search_by_phone(pg_session_factory):
    session = pg_session_factory()
    unique = uuid.uuid4().hex[:8]
    contact = _contact(session, phone_suffix=unique, first_name="جستجو")
    session.commit()

    response = client.get(f"/contacts?q={unique}", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] >= 1
    assert any(item["contact_id"] == contact.id for item in body["items"])


def test_list_contacts_import_batch_filter(pg_session_factory):
    session = pg_session_factory()
    batch = _import_batch(session, file_name="september.xlsx")
    imported = _contact(session, source_import_id=batch.id, first_name="imported")
    other = _contact(session, first_name="other")
    session.commit()

    response = client.get(f"/contacts?import_batch_id={batch.id}", headers=AUTH)
    assert response.status_code == 200
    ids = {item["contact_id"] for item in response.json()["items"]}
    assert imported.id in ids
    assert other.id not in ids


def test_list_contacts_import_provenance_fields(pg_session_factory):
    session = pg_session_factory()
    batch = _import_batch(session, file_name="history.xlsx")
    contact = _contact(session, source_import_id=batch.id)
    duplicate_row = ImportRow(
        batch_id=batch.id,
        row_index=2,
        status=ImportRowStatus.DUPLICATE,
        is_valid=False,
        duplicate_of_contact_id=contact.id,
    )
    session.add(duplicate_row)
    session.commit()

    response = client.get(f"/contacts?q={contact.phone[-8:]}", headers=AUTH)
    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["contact_id"] == contact.id)
    assert item["source_import_file_name"] == "history.xlsx"
    assert item["import_count"] == 2


def test_viewer_can_list_contacts(pg_session_factory):
    session = pg_session_factory()
    _contact(session)
    session.commit()

    async def _fake_viewer():
        return {"username": "viewer", "password": "viewer123", "role": "viewer"}

    app.dependency_overrides[get_current_user] = _fake_viewer
    try:
        response = client.get("/contacts", headers=AUTH)
        assert response.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)
