"""Contact tag list/filter and tag-audience campaign API."""

from __future__ import annotations

import uuid

import pandas as pd
from fastapi.testclient import TestClient

from core_engine.main import app
from core_engine.models import CampaignRecipient, ConsentStatus, Contact, PlatformType

client = TestClient(app)
AUTH = {"Authorization": "Bearer fake_token"}


def _contact(session, phone: str, tags: list[str], *, name: str, **kwargs) -> Contact:
    row = Contact(
        first_name=name,
        phone=phone,
        phone_e164=phone,
        consent_status=kwargs.pop("consent_status", ConsentStatus.ALLOWED.value),
        blacklisted=kwargs.pop("blacklisted", False),
        tags=tags,
        **kwargs,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def test_list_returns_tags_and_tag_filter_paginates(pg_session_factory):
    session = pg_session_factory()
    suffix = uuid.uuid4().hex[:8]
    tag = f"api-gold-{suffix}"
    other = f"api-other-{suffix}"
    first = _contact(session, f"+98931{suffix[:7]}", [tag], name=f"A-{suffix}")
    second = _contact(session, f"+98932{suffix[:7]}", [tag, other], name=f"B-{suffix}")
    _contact(session, f"+98933{suffix[:7]}", [other], name=f"C-{suffix}")

    listed = client.get("/contacts", params={"tags": tag, "limit": 50}, headers=AUTH)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    ids = {item["contact_id"] for item in body["items"]}
    assert first.id in ids
    assert second.id in ids
    by_id = {item["contact_id"]: item for item in body["items"]}
    assert tag in by_id[first.id]["tags"]
    assert body["total_count"] >= 2

    page = client.get(
        "/contacts",
        params={"tags": tag, "q": f"B-{suffix}", "limit": 1, "offset": 0, "tag_match": "all"},
        headers=AUTH,
    )
    assert page.status_code == 200, page.text
    page_body = page.json()
    assert page_body["total_count"] == 1
    assert page_body["items"][0]["contact_id"] == second.id
    assert page_body["limit"] == 1


def test_manual_patch_replaces_tags(pg_session_factory):
    session = pg_session_factory()
    suffix = uuid.uuid4().hex[:8]
    contact = _contact(session, f"+98934{suffix[:7]}", ["قدیمی", " قدیم "], name="edit")
    response = client.patch(
        f"/contacts/{contact.id}",
        json={"tags": ["  جدید  ", "جدید", ""]},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    assert response.json()["tags"] == ["جدید"]
    session.expire_all()
    refreshed = session.get(Contact, contact.id)
    assert refreshed.tags == ["جدید"]


def test_tag_audience_preview_matches_created_recipients(pg_session_factory):
    session = pg_session_factory()
    suffix = uuid.uuid4().hex[:8]
    gold, tehran = f"aud-g-{suffix}", f"aud-t-{suffix}"
    ali = _contact(session, f"+98935{suffix[:7]}", [gold, tehran], name="Ali")
    _contact(session, f"+98936{suffix[:7]}", [gold], name="B")
    _contact(session, f"+98937{suffix[:7]}", [tehran], name="C")

    preview = client.post(
        "/campaigns/audience-preview",
        json={"selected_tags": [gold, tehran], "tag_match": "all"},
        headers=AUTH,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["eligible_count"] == 1
    assert preview.json()["contact_ids"] == [ali.id]

    created = client.post(
        "/campaigns/from-tags",
        json={
            "selected_tags": [f" {gold} ", tehran, gold],
            "tag_match": "all",
            "title": f"tag-aud-{suffix}",
            "platform": PlatformType.RUBIKA.value,
            "template_text": "سلام",
            "account_ids": [],
        },
        headers=AUTH,
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["contacts_attached_count"] == preview.json()["eligible_count"]
    session.expire_all()
    recipients = (
        session.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == payload["campaign_id"])
        .all()
    )
    assert [row.contact_id for row in recipients] == [ali.id]

    edited = client.patch(
        f"/contacts/{ali.id}",
        json={"tags": ["دیگر"]},
        headers=AUTH,
    )
    assert edited.status_code == 200
    session.expire_all()
    remaining = (
        session.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == payload["campaign_id"])
        .all()
    )
    assert [row.contact_id for row in remaining] == [ali.id]


def test_empty_tag_audience_rejected():
    response = client.post(
        "/campaigns/audience-preview",
        json={"selected_tags": ["   "], "tag_match": "any"},
        headers=AUTH,
    )
    assert response.status_code == 400
    assert "tag" in response.json()["detail"].lower()


def test_commit_import_merges_tags_across_files(pg_session_factory, tmp_path):
    digits = "".join(ch for ch in uuid.uuid4().hex if ch.isdigit())
    local = "0912" + (digits + "0000000")[:7]
    canonical = "+98" + local[1:]
    first = tmp_path / "a.xlsx"
    second = tmp_path / "b.xlsx"
    pd.DataFrame([["Ali", local, "طلایی"]], columns=["نام", "شماره", "برچسب"]).to_excel(
        first, index=False
    )
    pd.DataFrame([["Ali", canonical, "تهران"]], columns=["نام", "شماره", "C"]).to_excel(
        second, index=False
    )

    created = client.post(
        "/imports/contacts/commit",
        json={
            "file_path": str(first),
            "original_file_name": "a.xlsx",
            "stored_file_name": "a.xlsx",
        },
        headers=AUTH,
    )
    assert created.status_code == 200, created.text
    assert created.json()["created_contacts_count"] == 1

    merged = client.post(
        "/imports/contacts/commit",
        json={
            "file_path": str(second),
            "original_file_name": "b.xlsx",
            "stored_file_name": "b.xlsx",
        },
        headers=AUTH,
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["created_contacts_count"] == 0
    assert merged.json()["duplicate_rows_count"] == 1

    session = pg_session_factory()
    rows = session.query(Contact).filter(Contact.phone_e164 == canonical).all()
    assert len(rows) == 1
    assert rows[0].tags == ["طلایی", "تهران"]
    assert rows[0].first_name == "Ali"
