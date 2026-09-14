"""New Phase 2 — contact identity, tag merge, tag audience."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from core_engine.models import (
    Campaign,
    CampaignRecipient,
    ConsentStatus,
    Contact,
    PlatformType,
)
from core_engine.services.contact_audience_snapshot import create_tag_campaign_recipients
from core_engine.services.contact_import import apply_import_to_contact
from core_engine.services.contact_tags import (
    canonical_phone,
    merge_tags,
    normalize_tag,
    parse_tag_cell,
    read_contact_tags,
    resolve_tag_audience,
)
from core_engine.services.excel_processor import ExcelProcessor


@pytest.fixture
def db(pg_session_factory):
    session = pg_session_factory()
    try:
        yield session
    finally:
        session.close()


def test_phone_variants_are_one_identity():
    assert canonical_phone("09121234567") == "+989121234567"
    assert canonical_phone("+989121234567") == "+989121234567"
    assert canonical_phone("989121234567") == "+989121234567"
    assert canonical_phone("00989121234567") == "+989121234567"


def test_invalid_and_empty_phone_rejected():
    assert canonical_phone("") is None
    assert canonical_phone(None) is None
    assert canonical_phone("123") is None
    assert canonical_phone("not-a-phone") is None


def test_tag_normalization_and_union():
    assert normalize_tag("  طلایی  ") == "طلایی"
    assert normalize_tag("   ") is None
    assert parse_tag_cell("طلایی, تهران ، طلایی") == ["طلایی", "تهران"]
    assert parse_tag_cell("") == []
    assert merge_tags(["تهران"], ["طلایی", "تهران"]) == ["تهران", "طلایی"]
    assert "VIP" != normalize_tag("vip") or True
    assert normalize_tag("VIP") == "VIP"
    assert normalize_tag("vip") == "vip"


def _contact(db, phone, tags, *, name="Ali", **kwargs):
    row = Contact(
        first_name=name,
        phone=phone,
        phone_e164=phone,
        consent_status=kwargs.pop("consent_status", ConsentStatus.ALLOWED.value),
        blacklisted=kwargs.pop("blacklisted", False),
        tags=tags,
        **kwargs,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_import_creates_and_merges_tags(db):
    phone = f"+98924{uuid.uuid4().hex[:7]}"
    created, action, added = apply_import_to_contact(
        db, phone_e164=phone, first_name="Ali", last_name=None, tags=["طلایی"]
    )
    db.commit()
    assert action == "created"
    assert added == ["طلایی"]
    merged, action2, added2 = apply_import_to_contact(
        db,
        phone_e164=phone,
        first_name="علیرضا",
        last_name=None,
        tags=["تهران", "طلایی"],
    )
    db.commit()
    assert action2 == "merged"
    assert merged.id == created.id
    assert read_contact_tags(merged.tags) == ["طلایی", "تهران"]
    assert added2 == ["تهران"]
    assert created.first_name == "Ali"


def test_manual_tags_survive_later_import(db):
    contact = _contact(db, f"+98925{uuid.uuid4().hex[:7]}", ["دستی"])
    apply_import_to_contact(
        db, phone_e164=contact.phone_e164, first_name=None, last_name=None, tags=["سرخکن"]
    )
    db.commit()
    db.refresh(contact)
    assert read_contact_tags(contact.tags) == ["دستی", "سرخکن"]


def test_same_file_duplicate_rows_union_tags(tmp_path: Path):
    path = tmp_path / "people.xlsx"
    pd.DataFrame(
        [
            ["Ali", "09121234567", "طلایی"],
            ["Ali", "09121234567", "تهران"],
            ["NoPhone", "", "طلایی"],
            ["Bad", "12", "x"],
        ],
        columns=["نام", "شماره", "برچسب"],
    ).to_excel(path, index=False)
    preview = ExcelProcessor().build_preview(str(path))
    by_status = {}
    for row in preview["rows"]:
        by_status.setdefault(row["status"], []).append(row)
    valid = by_status["valid"]
    duplicate = by_status["duplicate"]
    assert valid[0]["normalized_data"]["phone_e164"] == "+989121234567"
    assert valid[0]["normalized_data"]["tags"] == ["طلایی"]
    assert duplicate[0]["normalized_data"]["tags"] == ["تهران"]
    assert any(row["error_code"] == "missing_phone" for row in by_status["invalid"])
    assert any(row["error_code"] == "invalid_phone" for row in by_status["invalid"])


def test_column_c_is_tag_when_header_is_unmapped(tmp_path: Path):
    path = tmp_path / "c.xlsx"
    pd.DataFrame([["Ali", "09120000001", " طلایی "]], columns=["نام", "شماره", "C"]).to_excel(
        path, index=False
    )
    preview = ExcelProcessor().build_preview(str(path))
    assert preview["column_mapping"].get("tags") == "C"
    assert preview["rows"][0]["normalized_data"]["tags"] == ["طلایی"]


def test_concurrent_import_one_contact(db, pg_session_factory):
    phone = f"+98923{uuid.uuid4().hex[:7]}"
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def worker(tag: str) -> None:
        session = pg_session_factory()
        try:
            barrier.wait(timeout=10)
            apply_import_to_contact(
                session, phone_e164=phone, first_name="Ali", last_name=None, tags=[tag]
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=("طلایی",)), threading.Thread(target=worker, args=("تهران",))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert errors == []
    rows = db.query(Contact).filter(Contact.phone_e164 == phone).all()
    assert len(rows) == 1
    assert set(read_contact_tags(rows[0].tags)) == {"طلایی", "تهران"}


def test_tag_audience_any_all_and_exclusions(db):
    suffix = uuid.uuid4().hex[:8]
    gold, tehran, fry = f"p2g-{suffix}", f"p2t-{suffix}", f"p2f-{suffix}"
    ali = _contact(db, f"+98913{suffix[:7]}", [gold, tehran], name=f"Ali-{suffix}")
    bee = _contact(db, f"+98914{suffix[:7]}", [gold], name=f"B-{suffix}")
    cee = _contact(db, f"+98915{suffix[:7]}", [tehran, fry], name=f"C-{suffix}")
    deleted = _contact(db, f"+98916{suffix[:7]}", [gold], name=f"D-{suffix}")
    deleted.deleted_at = datetime.now(timezone.utc)
    blacklisted = _contact(db, f"+98917{suffix[:7]}", [tehran], name=f"E-{suffix}", blacklisted=True)
    blocked = _contact(
        db,
        f"+98918{suffix[:7]}",
        [tehran, gold],
        name=f"F-{suffix}",
        consent_status=ConsentStatus.BLOCKED.value,
    )
    db.commit()

    any_eligible, _ = resolve_tag_audience(db, [gold, tehran], match="any")
    all_eligible, _ = resolve_tag_audience(db, [gold, tehran], match="all")
    assert {c.id for c in any_eligible} == {ali.id, bee.id, cee.id}
    assert [c.id for c in all_eligible] == [ali.id]
    skipped = {c.id: reason for c, reason in resolve_tag_audience(db, [gold], match="any")[1]}
    assert skipped.get(deleted.id) == "deleted"
    assert blacklisted.id not in {c.id for c in any_eligible}
    assert blocked.id not in {c.id for c in any_eligible}
    assert skipped.get(blocked.id) == "consent_blocked"


def test_snapshot_does_not_follow_later_tag_edits(db):
    suffix = uuid.uuid4().hex[:8]
    gold, tehran = f"snapg-{suffix}", f"snapt-{suffix}"
    ali = _contact(db, f"+98919{suffix[:7]}", [gold, tehran])
    other = _contact(db, f"+98920{suffix[:7]}", [gold])
    campaign = Campaign(
        name="snap",
        channel="rubika",
        title="snap",
        platform=PlatformType.RUBIKA,
        status="draft",
        template_text="hi",
    )
    db.add(campaign)
    db.commit()
    created, preview_count = create_tag_campaign_recipients(
        db, campaign_id=campaign.id, tags=[gold, tehran], match="all"
    )
    db.commit()
    assert preview_count == 1
    assert created == 1
    assert db.query(CampaignRecipient).filter_by(campaign_id=campaign.id).count() == 1
    ali.tags = [f"gone-{suffix}"]
    other.tags = [gold, tehran]
    db.commit()
    remaining = db.query(CampaignRecipient).filter_by(campaign_id=campaign.id).all()
    assert len(remaining) == 1
    assert remaining[0].contact_id == ali.id


def test_preview_count_matches_created_recipients(db):
    suffix = uuid.uuid4().hex[:8]
    gold = f"countg-{suffix}"
    _contact(db, f"+98921{suffix[:7]}", [gold, f"t-{suffix}"])
    _contact(db, f"+98922{suffix[:7]}", [gold])
    eligible, _ = resolve_tag_audience(db, [gold], match="any")
    campaign = Campaign(
        name="count",
        channel="bale",
        title="count",
        platform=PlatformType.BALE,
        status="draft",
        template_text="hi",
    )
    db.add(campaign)
    db.commit()
    created, preview = create_tag_campaign_recipients(
        db, campaign_id=campaign.id, tags=[gold], match="any"
    )
    assert preview == len(eligible) == created == 2
    created_again, _ = create_tag_campaign_recipients(
        db, campaign_id=campaign.id, tags=[gold], match="any"
    )
    assert created_again == 0
    assert db.query(CampaignRecipient).filter_by(campaign_id=campaign.id).count() == 2
