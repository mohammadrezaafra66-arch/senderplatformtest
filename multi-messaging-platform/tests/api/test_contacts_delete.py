"""Contact soft-delete API tests."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from core_engine.api.auth import get_current_user
from core_engine.main import app
from core_engine.models import (
    AuditLog,
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    ConsentStatus,
    Contact,
    ImportBatch,
    ImportRow,
    ImportRowStatus,
    ImportStatus,
    Message,
    MessageAttempt,
    MessageAttemptStatus,
    PlatformType,
)

client = TestClient(app)
AUTH = {"Authorization": "Bearer fake_token"}


def _contact(
    session,
    *,
    phone_suffix: str | None = None,
    first_name: str = "آزمایش",
) -> Contact:
    suffix = phone_suffix or uuid.uuid4().hex[:8]
    contact = Contact(
        first_name=first_name,
        phone=f"+9891{suffix[:8]}",
        phone_e164=f"+9891{suffix[:8]}",
        consent_status=ConsentStatus.ALLOWED.value,
    )
    session.add(contact)
    session.flush()
    return contact


def _campaign(session) -> Campaign:
    campaign = Campaign(
        name=f"del-{uuid.uuid4().hex[:8]}",
        title=f"del-{uuid.uuid4().hex[:8]}",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        status=CampaignStatus.DRAFT.value,
        template_text="hi",
    )
    session.add(campaign)
    session.flush()
    return campaign


def test_delete_contact_soft_deletes(pg_session_factory):
    session = pg_session_factory()
    contact = _contact(session)
    session.commit()

    response = client.post(f"/contacts/{contact.id}/delete", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["already_deleted"] is False
    assert body["deleted_at"] is not None

    session.refresh(contact)
    assert contact.deleted_at is not None


def test_delete_contact_idempotent(pg_session_factory):
    session = pg_session_factory()
    contact = _contact(session)
    session.commit()

    first = client.post(f"/contacts/{contact.id}/delete", headers=AUTH)
    second = client.post(f"/contacts/{contact.id}/delete", headers=AUTH)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["already_deleted"] is True


def test_deleted_contact_excluded_from_list(pg_session_factory):
    session = pg_session_factory()
    contact = _contact(session, first_name="hidden")
    session.commit()

    client.post(f"/contacts/{contact.id}/delete", headers=AUTH)

    listing = client.get("/contacts", headers=AUTH)
    assert listing.status_code == 200
    ids = {item["contact_id"] for item in listing.json()["items"]}
    assert contact.id not in ids


def test_total_count_decreases_after_delete(pg_session_factory):
    session = pg_session_factory()
    contact = _contact(session)
    session.commit()

    before = client.get("/contacts", headers=AUTH).json()["total_count"]
    client.post(f"/contacts/{contact.id}/delete", headers=AUTH)
    after = client.get("/contacts", headers=AUTH).json()["total_count"]
    assert after == before - 1


def test_search_excludes_deleted_contact(pg_session_factory):
    session = pg_session_factory()
    suffix = uuid.uuid4().hex[:8]
    contact = _contact(session, phone_suffix=suffix, first_name="search-delete")
    session.commit()

    client.post(f"/contacts/{contact.id}/delete", headers=AUTH)

    search = client.get(f"/contacts/search?q={suffix}", headers=AUTH)
    assert search.status_code == 200
    ids = {item["contact_id"] for item in search.json()["items"]}
    assert contact.id not in ids


def test_campaign_recipient_preserved_after_delete(pg_session_factory):
    session = pg_session_factory()
    contact = _contact(session)
    campaign = _campaign(session)
    recipient = CampaignRecipient(
        campaign_id=campaign.id,
        contact_id=contact.id,
    )
    session.add(recipient)
    session.commit()
    recipient_id = recipient.id

    client.post(f"/contacts/{contact.id}/delete", headers=AUTH)

    session.expire_all()
    preserved = session.query(CampaignRecipient).filter(CampaignRecipient.id == recipient_id).first()
    assert preserved is not None
    assert preserved.contact_id == contact.id


def test_message_history_preserved_after_delete(pg_session_factory):
    session = pg_session_factory()
    contact = _contact(session)
    campaign = _campaign(session)
    from core_engine.models import Account, AccountStatus

    acct = Account(
        platform=PlatformType.RUBIKA,
        status=AccountStatus.ACTIVE,
        label="hist",
        phone_number=f"09{uuid.uuid4().int % 10**9:09d}",
    )
    session.add(acct)
    session.flush()
    message = Message(
        campaign_id=campaign.id,
        account_id=acct.id,
        contact_id=contact.id,
        dedupe_key=f"dedupe-{uuid.uuid4().hex}",
        rendered_text="hello",
    )
    session.add(message)
    session.flush()
    attempt = MessageAttempt(
        message_id=message.id,
        attempt_no=1,
        status=MessageAttemptStatus.SUCCESS,
    )
    session.add(attempt)
    session.commit()
    message_id = message.id

    client.post(f"/contacts/{contact.id}/delete", headers=AUTH)

    session.expire_all()
    assert session.query(Message).filter(Message.id == message_id).first() is not None


def test_deleted_contact_not_eligible_for_new_campaign(pg_session_factory):
    session = pg_session_factory()
    contact = _contact(session)
    session.commit()

    client.post(f"/contacts/{contact.id}/delete", headers=AUTH)

    response = client.post(
        "/campaigns/from-contacts",
        headers=AUTH,
        json={
            "contact_ids": [contact.id],
            "title": "should-skip",
            "platform": "rubika",
            "template_text": "hi",
            "use_gpt": False,
            "include_products": False,
            "account_ids": [],
        },
    )
    assert response.status_code == 400


def test_reimport_deleted_phone_reactivates_contact(pg_session_factory):
    session = pg_session_factory()
    suffix = uuid.uuid4().hex[:10]
    phone = f"+9891{suffix[:8]}"
    contact = Contact(
        first_name="Old",
        phone=phone,
        phone_e164=phone,
        consent_status=ConsentStatus.ALLOWED.value,
        deleted_at=datetime.utcnow(),
        deleted_by="tester",
    )
    session.add(contact)
    batch = ImportBatch(
        file_name="restore.xlsx",
        original_file_name="restore.xlsx",
        status=ImportStatus.PENDING,
        row_count=1,
    )
    session.add(batch)
    session.flush()
    row = ImportRow(
        batch_id=batch.id,
        row_index=1,
        status=ImportRowStatus.VALID,
        is_valid=True,
        normalized_data={"phone_e164": phone, "first_name": "Restored"},
    )
    session.add(row)
    session.commit()

    from core_engine.services.contact_delete import reactivate_deleted_contact_from_import

    reactivate_deleted_contact_from_import(
        contact,
        import_batch_id=batch.id,
        import_row_id=row.id,
        normalized={"phone_e164": phone, "first_name": "Restored"},
    )
    session.commit()
    session.refresh(contact)

    assert contact.deleted_at is None
    assert contact.first_name == "Restored"

    listing = client.get(f"/contacts?q={suffix[:8]}", headers=AUTH)
    ids = {item["contact_id"] for item in listing.json()["items"]}
    assert contact.id in ids


def test_viewer_cannot_delete_contact(pg_session_factory):
    session = pg_session_factory()
    contact = _contact(session)
    session.commit()

    async def _fake_viewer():
        return {"username": "viewer", "password": "viewer123", "role": "viewer"}

    app.dependency_overrides[get_current_user] = _fake_viewer
    try:
        response = client.post(f"/contacts/{contact.id}/delete", headers=AUTH)
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    session.refresh(contact)
    assert contact.deleted_at is None


def test_delete_records_audit_event(pg_session_factory):
    session = pg_session_factory()
    contact = _contact(session)
    session.commit()

    client.post(f"/contacts/{contact.id}/delete", headers=AUTH)

    audit = (
        session.query(AuditLog)
        .filter(AuditLog.action == "contact_deleted", AuditLog.resource_id == str(contact.id))
        .first()
    )
    assert audit is not None
