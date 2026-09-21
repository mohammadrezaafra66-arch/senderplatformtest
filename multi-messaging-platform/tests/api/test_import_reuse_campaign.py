"""Re-import of existing phones must remain campaign-eligible without duplicating Contact."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from core_engine.main import app
from core_engine.models import (
    CampaignRecipient,
    ConsentStatus,
    Contact,
    ImportBatch,
    ImportRow,
    ImportRowStatus,
    ImportStatus,
    PlatformType,
)
from core_engine.services.contact_import import apply_import_to_contact
from tests.api.test_campaign_from_contacts import _account, _cleanup_created_entities

client = TestClient(app)
AUTH = {"Authorization": "Bearer fake_token"}


def _phone() -> str:
    return f"+9891{uuid.uuid4().int % 10**8:08d}"


def _xlsx(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    path = tmp_path / f"reimport-{uuid.uuid4().hex[:8]}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["phone", "first_name"])
    for phone, first_name in rows:
        ws.append([phone, first_name])
    wb.save(path)
    return path


def _commit_xlsx(path: Path) -> dict:
    with path.open("rb") as handle:
        preview = client.post(
            "/imports/contacts/preview",
            headers=AUTH,
            files={
                "file": (
                    path.name,
                    handle,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    commit = client.post(
        "/imports/contacts/commit",
        headers=AUTH,
        json={
            "file_path": body["file_path"],
            "original_file_name": body["original_file_name"],
            "stored_file_name": body["stored_file_name"],
            "sheet_name": body.get("sheet_name"),
            "uploaded_by": "reimport-test",
        },
    )
    assert commit.status_code == 200, commit.text
    return commit.json()


def _from_import(session, batch_id: int, account_id: int) -> dict:
    title = f"reimport-camp-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/campaigns/from-import",
        json={
            "import_batch_id": batch_id,
            "title": title,
            "platform": PlatformType.RUBIKA.value,
            "template_text": "سلام {{first_name}}",
            "use_gpt": False,
            "include_products": False,
            "account_ids": [account_id],
        },
        headers=AUTH,
    )
    return {"status_code": resp.status_code, "body": resp.json(), "title": title}


def _wipe_import_rows(session, import_batch_id: int | None) -> None:
    if import_batch_id is None:
        return
    session.query(ImportRow).filter(ImportRow.batch_id == import_batch_id).delete(
        synchronize_session=False
    )


def _finish(
    session,
    *,
    account_id: int | None = None,
    contact_ids: list[int] | None = None,
    campaign_id: int | None = None,
    import_batch_id: int | None = None,
) -> None:
    _cleanup_created_entities(
        session,
        account_id=account_id,
        contact_ids=contact_ids,
        campaign_id=campaign_id,
        import_batch_id=None,
    )
    _wipe_import_rows(session, import_batch_id)
    if import_batch_id is not None:
        session.query(ImportBatch).filter(ImportBatch.id == import_batch_id).delete(
            synchronize_session=False
        )
        session.commit()


def test_new_phone_creates_contact_and_is_campaign_eligible(
    client, pg_session_factory, tmp_path
):
    session = pg_session_factory()
    account_id = None
    contact_ids: list[int] = []
    campaign_id = None
    import_batch_id = None
    try:
        account = _account(session)
        account_id = account.id
        session.commit()
        phone = _phone()
        result = _commit_xlsx(_xlsx(tmp_path, [(phone, "جدید")]))
        import_batch_id = result["import_batch_id"]
        assert result["created_contacts_count"] == 1
        contact = session.query(Contact).filter(Contact.phone_e164 == phone).one()
        contact_ids = [contact.id]
        assert contact.source_import_id == import_batch_id
        out = _from_import(session, import_batch_id, account_id)
        assert out["status_code"] == 200, out["body"]
        campaign_id = out["body"]["campaign_id"]
        assert out["body"]["contacts_attached_count"] == 1
        recipients = (
            session.query(CampaignRecipient)
            .filter(CampaignRecipient.campaign_id == campaign_id)
            .all()
        )
        assert [row.contact_id for row in recipients] == [contact.id]
    finally:
        _finish(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            campaign_id=campaign_id,
            import_batch_id=import_batch_id,
        )
        session.close()


def test_existing_phone_reuses_contact_and_is_campaign_eligible(
    client, pg_session_factory, tmp_path
):
    session = pg_session_factory()
    account_id = None
    contact_ids: list[int] = []
    campaign_id = None
    import_batch_id = None
    try:
        account = _account(session)
        account_id = account.id
        phone = _phone()
        existing = Contact(
            first_name="قبلی",
            phone=phone,
            phone_e164=phone,
            consent_status=ConsentStatus.ALLOWED.value,
        )
        session.add(existing)
        session.commit()
        contact_ids = [existing.id]
        before = session.query(Contact).filter(Contact.phone_e164 == phone).count()
        result = _commit_xlsx(_xlsx(tmp_path, [(phone, "")]))
        import_batch_id = result["import_batch_id"]
        assert result["created_contacts_count"] == 0
        assert result["duplicate_rows_count"] == 1
        session.expire_all()
        after = session.query(Contact).filter(Contact.phone_e164 == phone).all()
        assert len(after) == before == 1
        assert after[0].id == existing.id
        assert after[0].first_name == "قبلی"
        assert after[0].source_import_id != import_batch_id
        row = (
            session.query(ImportRow)
            .filter(ImportRow.batch_id == import_batch_id)
            .one()
        )
        assert row.duplicate_of_contact_id == existing.id
        out = _from_import(session, import_batch_id, account_id)
        assert out["status_code"] == 200, out["body"]
        campaign_id = out["body"]["campaign_id"]
        assert out["body"]["contacts_attached_count"] == 1
        recipients = (
            session.query(CampaignRecipient)
            .filter(CampaignRecipient.campaign_id == campaign_id)
            .all()
        )
        assert [row.contact_id for row in recipients] == [existing.id]
    finally:
        _finish(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            campaign_id=campaign_id,
            import_batch_id=import_batch_id,
        )
        session.close()


def test_same_file_duplicate_makes_one_contact_and_one_recipient(
    client, pg_session_factory, tmp_path
):
    session = pg_session_factory()
    account_id = None
    contact_ids: list[int] = []
    campaign_id = None
    import_batch_id = None
    try:
        account = _account(session)
        account_id = account.id
        session.commit()
        phone = _phone()
        result = _commit_xlsx(_xlsx(tmp_path, [(phone, "A"), (phone, "B")]))
        import_batch_id = result["import_batch_id"]
        contacts = session.query(Contact).filter(Contact.phone_e164 == phone).all()
        assert len(contacts) == 1
        contact_ids = [contacts[0].id]
        assert result["duplicate_rows_count"] >= 1
        out = _from_import(session, import_batch_id, account_id)
        assert out["status_code"] == 200, out["body"]
        campaign_id = out["body"]["campaign_id"]
        assert out["body"]["contacts_attached_count"] == 1
        recipients = (
            session.query(CampaignRecipient)
            .filter(CampaignRecipient.campaign_id == campaign_id)
            .all()
        )
        assert len(recipients) == 1
        assert recipients[0].contact_id == contacts[0].id
    finally:
        _finish(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            campaign_id=campaign_id,
            import_batch_id=import_batch_id,
        )
        session.close()


def test_existing_phone_with_in_file_duplicate_stays_one_recipient(
    client, pg_session_factory, tmp_path
):
    session = pg_session_factory()
    account_id = None
    contact_ids: list[int] = []
    campaign_id = None
    import_batch_id = None
    try:
        account = _account(session)
        account_id = account.id
        phone = _phone()
        existing = Contact(
            first_name="موجود",
            phone=phone,
            phone_e164=phone,
            consent_status=ConsentStatus.ALLOWED.value,
        )
        session.add(existing)
        session.commit()
        contact_ids = [existing.id]
        result = _commit_xlsx(_xlsx(tmp_path, [(phone, "X"), (phone, "Y")]))
        import_batch_id = result["import_batch_id"]
        assert session.query(Contact).filter(Contact.phone_e164 == phone).count() == 1
        out = _from_import(session, import_batch_id, account_id)
        assert out["status_code"] == 200, out["body"]
        campaign_id = out["body"]["campaign_id"]
        assert out["body"]["contacts_attached_count"] == 1
    finally:
        _finish(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            campaign_id=campaign_id,
            import_batch_id=import_batch_id,
        )
        session.close()


def test_invalid_phone_is_not_imported_or_attached(client, pg_session_factory, tmp_path):
    session = pg_session_factory()
    account_id = None
    campaign_id = None
    import_batch_id = None
    try:
        account = _account(session)
        account_id = account.id
        session.commit()
        result = _commit_xlsx(_xlsx(tmp_path, [("12", "بد")]))
        import_batch_id = result["import_batch_id"]
        assert result["created_contacts_count"] == 0
        assert result["invalid_rows_count"] == 1
        row = (
            session.query(ImportRow)
            .filter(ImportRow.batch_id == import_batch_id)
            .one()
        )
        assert row.status == ImportRowStatus.INVALID
        assert row.error_code == "invalid_phone"
        out = _from_import(session, import_batch_id, account_id)
        assert out["status_code"] == 400
        assert "No eligible contacts found for this import" in str(out["body"])
    finally:
        _finish(
            session,
            account_id=account_id,
            campaign_id=campaign_id,
            import_batch_id=import_batch_id,
        )
        session.close()


def test_blacklisted_and_blocked_reused_contacts_are_excluded(
    client, pg_session_factory
):
    session = pg_session_factory()
    account_id = None
    contact_ids: list[int] = []
    import_batch_id = None
    try:
        account = _account(session)
        account_id = account.id
        blocked_phone = _phone()
        banned_phone = _phone()
        blocked = Contact(
            first_name="blocked",
            phone=blocked_phone,
            phone_e164=blocked_phone,
            consent_status=ConsentStatus.BLOCKED.value,
        )
        banned = Contact(
            first_name="banned",
            phone=banned_phone,
            phone_e164=banned_phone,
            consent_status=ConsentStatus.ALLOWED.value,
            blacklisted=True,
        )
        session.add_all([blocked, banned])
        batch = ImportBatch(
            file_name=f"{uuid.uuid4().hex}.xlsx",
            status=ImportStatus.COMMITTED,
            committed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(batch)
        session.flush()
        session.add_all(
            [
                ImportRow(
                    batch_id=batch.id,
                    row_index=1,
                    status=ImportRowStatus.DUPLICATE,
                    is_valid=True,
                    error_code="merged_existing_contact",
                    duplicate_of_contact_id=blocked.id,
                ),
                ImportRow(
                    batch_id=batch.id,
                    row_index=2,
                    status=ImportRowStatus.DUPLICATE,
                    is_valid=True,
                    error_code="merged_existing_contact",
                    duplicate_of_contact_id=banned.id,
                ),
            ]
        )
        session.commit()
        contact_ids = [blocked.id, banned.id]
        import_batch_id = batch.id
        out = _from_import(session, import_batch_id, account_id)
        assert out["status_code"] == 400
        assert "No eligible contacts found for this import" in str(out["body"])
    finally:
        _finish(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            import_batch_id=import_batch_id,
        )
        session.close()


def test_deleted_reused_contact_is_excluded(client, pg_session_factory):
    session = pg_session_factory()
    account_id = None
    contact_ids: list[int] = []
    import_batch_id = None
    try:
        account = _account(session)
        account_id = account.id
        phone = _phone()
        deleted = Contact(
            first_name="حذف",
            phone=phone,
            phone_e164=phone,
            consent_status=ConsentStatus.ALLOWED.value,
            deleted_at=datetime.now(timezone.utc),
        )
        session.add(deleted)
        batch = ImportBatch(
            file_name=f"{uuid.uuid4().hex}.xlsx",
            status=ImportStatus.COMMITTED,
            committed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(batch)
        session.flush()
        session.add(
            ImportRow(
                batch_id=batch.id,
                row_index=1,
                status=ImportRowStatus.DUPLICATE,
                is_valid=True,
                duplicate_of_contact_id=deleted.id,
            )
        )
        session.commit()
        contact_ids = [deleted.id]
        import_batch_id = batch.id
        out = _from_import(session, import_batch_id, account_id)
        assert out["status_code"] == 400
    finally:
        _finish(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            import_batch_id=import_batch_id,
        )
        session.close()


def test_apply_import_twice_is_idempotent(pg_session_factory):
    session = pg_session_factory()
    try:
        phone = _phone()
        first, action, _ = apply_import_to_contact(
            session, phone_e164=phone, first_name="Ali", last_name=None, tags=[]
        )
        session.commit()
        second, action2, _ = apply_import_to_contact(
            session, phone_e164=phone, first_name="", last_name=None, tags=[]
        )
        session.commit()
        assert action == "created"
        assert action2 == "merged"
        assert first.id == second.id
        session.refresh(first)
        assert first.first_name == "Ali"
        assert session.query(Contact).filter(Contact.phone_e164 == phone).count() == 1
    finally:
        session.query(Contact).filter(Contact.phone_e164 == phone).delete(
            synchronize_session=False
        )
        session.commit()
        session.close()


def test_from_import_does_not_duplicate_recipients_for_same_contact(
    client, pg_session_factory
):
    session = pg_session_factory()
    account_id = None
    contact_ids: list[int] = []
    campaign_id = None
    import_batch_id = None
    try:
        account = _account(session)
        account_id = account.id
        phone = _phone()
        contact = Contact(
            first_name="یکتا",
            phone=phone,
            phone_e164=phone,
            consent_status=ConsentStatus.ALLOWED.value,
        )
        session.add(contact)
        batch = ImportBatch(
            file_name=f"{uuid.uuid4().hex}.xlsx",
            status=ImportStatus.COMMITTED,
            committed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(batch)
        session.flush()
        contact.source_import_id = batch.id
        session.add(
            ImportRow(
                batch_id=batch.id,
                row_index=2,
                status=ImportRowStatus.DUPLICATE,
                is_valid=True,
                duplicate_of_contact_id=contact.id,
            )
        )
        session.commit()
        contact_ids = [contact.id]
        import_batch_id = batch.id
        out = _from_import(session, import_batch_id, account_id)
        assert out["status_code"] == 200, out["body"]
        campaign_id = out["body"]["campaign_id"]
        assert out["body"]["contacts_attached_count"] == 1
        recipients = (
            session.query(CampaignRecipient)
            .filter(CampaignRecipient.campaign_id == campaign_id)
            .all()
        )
        assert len(recipients) == 1
    finally:
        _finish(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            campaign_id=campaign_id,
            import_batch_id=import_batch_id,
        )
        session.close()
