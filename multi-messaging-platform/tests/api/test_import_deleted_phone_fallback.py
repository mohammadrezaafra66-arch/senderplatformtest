"""Safety tests for invalid-phone → exact soft-deleted Contact reactivation fallback."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from core_engine.api.imports import (
    _exact_phone_identity_candidates,
    _extract_import_phone_raw,
    _lookup_deleted_contact_by_exact_phone,
)
from core_engine.main import app
from core_engine.models import ConsentStatus, Contact
from core_engine.services.contact_delete import is_contact_deleted

client = TestClient(app)
AUTH = {"Authorization": "Bearer fake_token"}


def _xlsx(tmp_path: Path, phone: str, first_name: str = "fallback") -> Path:
    path = tmp_path / f"import-{uuid.uuid4().hex[:8]}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["phone", "first_name"])
    ws.append([phone, first_name])
    wb.save(path)
    return path


def _preview_commit(path: Path) -> dict:
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
            "uploaded_by": "fallback-test",
        },
    )
    assert commit.status_code == 200, commit.text
    return {"preview": body, "commit": commit.json()}


def _soft_deleted(session, phone_e164: str, first_name: str = "deleted") -> Contact:
    contact = Contact(
        first_name=first_name,
        phone=phone_e164,
        phone_e164=phone_e164,
        consent_status=ConsentStatus.ALLOWED.value,
        deleted_at=datetime.utcnow(),
        deleted_by="tester",
    )
    session.add(contact)
    session.flush()
    return contact


def _invalid_canonical_phone() -> str:
    """Iran-looking number that fails IRAN_MOBILE_PATTERN (+989 + 10 digits)."""
    return f"+9899{uuid.uuid4().int % 10**9:09d}"


def test_extract_phone_raw_phone_column_only():
    assert _extract_import_phone_raw({"phone": "+989912345678", "first_name": "Ali"}) == "+989912345678"
    # Non-phone fields must never be scanned as phones.
    assert _extract_import_phone_raw({"first_name": "9123456789", "notes": "+9899"}) is None
    # Multiple phone cells → fail closed.
    assert (
        _extract_import_phone_raw({"phone": "+989912345678", "phone_whatsapp": "+989912345679"})
        is None
    )


def test_exact_identity_candidates_no_truncation():
    phone = "+9899180311611"
    assert _exact_phone_identity_candidates(phone) == [phone]
    assert _exact_phone_identity_candidates("9899180311611") == [
        "9899180311611",
        "+9899180311611",
    ]
    # Must not invent shortened/fuzzy forms.
    assert "989180311611" not in _exact_phone_identity_candidates(phone)


def test_normal_valid_phone_import_unchanged(pg_session_factory, tmp_path):
    session = pg_session_factory()
    before = session.query(Contact).count()
    phone = f"+9891{uuid.uuid4().int % 10**8:08d}"
    result = _preview_commit(_xlsx(tmp_path, phone, "normal-new"))
    assert result["commit"]["status"] == "committed"
    assert result["commit"]["created_contacts_count"] == 1
    session.expire_all()
    created = session.query(Contact).filter(Contact.phone_e164 == phone).one()
    assert created.deleted_at is None
    assert session.query(Contact).count() == before + 1


def test_malformed_no_match_rejects_without_mutation(pg_session_factory, tmp_path):
    session = pg_session_factory()
    before_ids = {c.id for c in session.query(Contact).all()}
    before_deleted = {
        c.id: c.deleted_at
        for c in session.query(Contact).filter(Contact.deleted_at.isnot(None)).all()
    }
    # Invalid Iranian length / pattern and not stored anywhere.
    result = _preview_commit(_xlsx(tmp_path, _invalid_canonical_phone(), "no-match"))
    assert result["commit"]["status"] == "failed"
    assert result["commit"]["created_contacts_count"] == 0
    assert result["commit"]["invalid_rows_count"] == 1
    session.expire_all()
    after_ids = {c.id for c in session.query(Contact).all()}
    assert after_ids == before_ids
    after_deleted = {
        c.id: c.deleted_at
        for c in session.query(Contact).filter(Contact.deleted_at.isnot(None)).all()
    }
    assert after_deleted.keys() == before_deleted.keys()


def test_malformed_exact_soft_deleted_reactivates(pg_session_factory, tmp_path):
    session = pg_session_factory()
    # Stored canonical itself fails IRAN_MOBILE_PATTERN (10 digits after +989).
    phone = _invalid_canonical_phone()
    contact = _soft_deleted(session, phone, first_name="old-deleted")
    session.commit()
    contact_id = contact.id

    result = _preview_commit(_xlsx(tmp_path, phone, "reactivated-name"))
    assert result["commit"]["status"] == "committed"
    assert result["commit"]["created_contacts_count"] == 1

    session.expire_all()
    restored = session.query(Contact).filter(Contact.id == contact_id).one()
    assert restored.deleted_at is None
    assert restored.first_name == "reactivated-name"
    assert session.query(Contact).filter(Contact.phone_e164 == phone).count() == 1


def test_malformed_active_match_does_not_duplicate(pg_session_factory, tmp_path):
    session = pg_session_factory()
    phone = _invalid_canonical_phone()
    active = Contact(
        first_name="active",
        phone=phone,
        phone_e164=phone,
        consent_status=ConsentStatus.ALLOWED.value,
    )
    session.add(active)
    session.commit()
    active_id = active.id

    result = _preview_commit(_xlsx(tmp_path, phone, "should-not-create"))
    # Preview marks invalid_phone; fallback refuses active matches → still invalid.
    assert result["commit"]["created_contacts_count"] == 0
    assert result["commit"]["invalid_rows_count"] == 1

    session.expire_all()
    assert session.query(Contact).filter(Contact.phone_e164 == phone).count() == 1
    assert session.query(Contact).filter(Contact.id == active_id).one().deleted_at is None


def test_ambiguous_identity_candidates_fail_closed(pg_session_factory):
    session = pg_session_factory()
    # Two distinct canonical rows that the candidate set could both hit.
    bare = f"9899{uuid.uuid4().int % 10**9:09d}"
    a = _soft_deleted(session, bare, first_name="a")
    b = _soft_deleted(session, f"+{bare}", first_name="b")
    session.commit()

    matched = _lookup_deleted_contact_by_exact_phone(session, bare)
    assert matched is None  # fail closed — two matches across identity forms
    session.refresh(a)
    session.refresh(b)
    assert is_contact_deleted(a)
    assert is_contact_deleted(b)


def test_unrelated_malformed_does_not_reactivate_other(pg_session_factory, tmp_path):
    session = pg_session_factory()
    other = _soft_deleted(session, _invalid_canonical_phone(), first_name="other-deleted")
    session.commit()
    other_id = other.id

    result = _preview_commit(_xlsx(tmp_path, _invalid_canonical_phone(), "unrelated"))
    assert result["commit"]["created_contacts_count"] == 0
    session.expire_all()
    assert session.query(Contact).filter(Contact.id == other_id).one().deleted_at is not None


def test_normal_reimport_of_valid_deleted_phone_still_works(pg_session_factory, tmp_path):
    session = pg_session_factory()
    phone = f"+9891{uuid.uuid4().int % 10**8:08d}"
    contact = _soft_deleted(session, phone, first_name="valid-deleted")
    session.commit()
    contact_id = contact.id

    result = _preview_commit(_xlsx(tmp_path, phone, "restored-valid"))
    assert result["commit"]["status"] == "committed"
    assert result["commit"]["created_contacts_count"] == 1
    session.expire_all()
    restored = session.query(Contact).filter(Contact.id == contact_id).one()
    assert restored.deleted_at is None
    assert session.query(Contact).filter(Contact.phone_e164 == phone).count() == 1


def test_duplicate_semantics_for_active_valid_phone(pg_session_factory, tmp_path):
    session = pg_session_factory()
    phone = f"+9891{uuid.uuid4().int % 10**8:08d}"
    session.add(
        Contact(
            first_name="exists",
            phone=phone,
            phone_e164=phone,
            consent_status=ConsentStatus.ALLOWED.value,
        )
    )
    session.commit()

    result = _preview_commit(_xlsx(tmp_path, phone, "dup"))
    assert result["commit"]["created_contacts_count"] == 0
    assert result["commit"]["duplicate_rows_count"] == 1
    assert session.query(Contact).filter(Contact.phone_e164 == phone).count() == 1
