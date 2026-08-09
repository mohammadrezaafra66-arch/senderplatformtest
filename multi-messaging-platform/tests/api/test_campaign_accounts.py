import uuid

import pytest
from sqlalchemy import event

from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    ImportBatch,
    ImportStatus,
    PlatformType,
)


AUTH = {"Authorization": "Bearer fake_token"}


def _account(session, platform=PlatformType.BALE, status=AccountStatus.ACTIVE):
    account = Account(
        platform=platform,
        status=status,
        label=f"sender-{uuid.uuid4().hex[:8]}",
        phone_number=f"09{uuid.uuid4().int % 10**9:09d}",
    )
    session.add(account)
    session.flush()
    return account


def _campaign(session, platform=PlatformType.BALE):
    campaign = Campaign(
        name=f"campaign-{uuid.uuid4().hex}",
        title="Campaign account test",
        channel=platform.value,
        platform=platform,
        status=CampaignStatus.DRAFT.value,
    )
    session.add(campaign)
    session.commit()
    return campaign


@pytest.fixture
def account_env(pg_session_factory):
    session = pg_session_factory()
    campaigns = []
    accounts = []
    yield session, campaigns, accounts
    campaign_ids = [campaign.id for campaign in campaigns]
    account_ids = [account.id for account in accounts]
    if campaign_ids:
        session.query(CampaignAccount).filter(
            CampaignAccount.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)
        session.query(Campaign).filter(Campaign.id.in_(campaign_ids)).delete(
            synchronize_session=False
        )
    if account_ids:
        session.query(Account).filter(Account.id.in_(account_ids)).delete(
            synchronize_session=False
        )
    session.commit()
    session.close()


def test_update_one_multiple_deduplicates_and_preserves_priority(client, account_env):
    session, campaigns, accounts = account_env
    campaign = _campaign(session)
    campaigns.append(campaign)
    accounts.extend([_account(session) for _ in range(3)])
    session.commit()

    response = client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [accounts[1].id, accounts[0].id, accounts[1].id, accounts[2].id]},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["account_ids"] == [accounts[1].id, accounts[0].id, accounts[2].id]
    assert [item["priority"] for item in response.json()["sender_accounts"]] == [1, 2, 3]
    assert all(item["weight"] == 1 and item["enabled"] for item in response.json()["sender_accounts"])


def test_update_replaces_is_idempotent_and_empty_means_auto(client, account_env):
    session, campaigns, accounts = account_env
    campaign = _campaign(session)
    campaigns.append(campaign)
    accounts.extend([_account(session) for _ in range(3)])
    session.commit()

    response = client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [accounts[0].id, accounts[1].id]},
        headers=AUTH,
    )
    assert response.status_code == 200
    session.expire_all()
    original_links = {
        link.account_id: (link.id, link.created_at)
        for link in session.query(CampaignAccount).filter_by(campaign_id=campaign.id)
    }

    for ids in ([accounts[1].id, accounts[2].id], [accounts[1].id, accounts[2].id]):
        response = client.put(
            f"/campaigns/{campaign.id}/accounts", json={"account_ids": ids}, headers=AUTH
        )
        assert response.status_code == 200
    assert session.query(CampaignAccount).filter_by(campaign_id=campaign.id).count() == 2
    session.expire_all()
    retained = session.query(CampaignAccount).filter_by(
        campaign_id=campaign.id, account_id=accounts[1].id
    ).one()
    assert (retained.id, retained.created_at) == original_links[accounts[1].id]

    response = client.put(
        f"/campaigns/{campaign.id}/accounts", json={"account_ids": []}, headers=AUTH
    )
    assert response.status_code == 200
    assert response.json()["account_ids"] == []
    session.expire_all()
    assert session.query(CampaignAccount).filter_by(campaign_id=campaign.id).count() == 0


def test_reordering_updates_priority_without_replacing_links(client, account_env):
    session, campaigns, accounts = account_env
    campaign = _campaign(session)
    campaigns.append(campaign)
    accounts.extend([_account(session) for _ in range(2)])
    session.commit()
    assert client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [accounts[0].id, accounts[1].id]},
        headers=AUTH,
    ).status_code == 200
    session.expire_all()
    original_ids = {
        link.account_id: link.id
        for link in session.query(CampaignAccount).filter_by(campaign_id=campaign.id)
    }

    response = client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [accounts[1].id, accounts[0].id]},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["account_ids"] == [accounts[1].id, accounts[0].id]
    assert [item["priority"] for item in response.json()["sender_accounts"]] == [1, 2]
    session.expire_all()
    reordered = session.query(CampaignAccount).filter_by(campaign_id=campaign.id).all()
    assert {link.account_id: link.id for link in reordered} == original_ids


@pytest.mark.parametrize("invalid_id", [0, -1])
def test_non_positive_account_ids_are_rejected(client, account_env, invalid_id):
    session, campaigns, _accounts = account_env
    campaign = _campaign(session)
    campaigns.append(campaign)
    response = client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [invalid_id]},
        headers=AUTH,
    )
    assert response.status_code == 422
    assert session.query(CampaignAccount).filter_by(campaign_id=campaign.id).count() == 0


@pytest.mark.parametrize(
    ("kind", "status_code", "error_code"),
    [
        ("missing", 404, "account_not_found"),
        ("inactive", 400, "account_inactive"),
        ("mismatch", 400, "account_platform_mismatch"),
    ],
)
def test_validation_errors_rollback(client, account_env, kind, status_code, error_code):
    session, campaigns, accounts = account_env
    campaign = _campaign(session)
    campaigns.append(campaign)
    valid = _account(session)
    accounts.append(valid)
    session.commit()
    assert client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [valid.id]},
        headers=AUTH,
    ).status_code == 200

    if kind == "missing":
        invalid_id = 2_000_000_000
    elif kind == "inactive":
        invalid = _account(session, status=AccountStatus.RESTING)
        accounts.append(invalid)
        session.commit()
        invalid_id = invalid.id
    else:
        invalid = _account(session, platform=PlatformType.RUBIKA)
        accounts.append(invalid)
        session.commit()
        invalid_id = invalid.id

    response = client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [invalid_id]},
        headers=AUTH,
    )
    assert response.status_code == status_code
    assert response.json()["detail"]["code"] == error_code
    session.expire_all()
    assert [row.account_id for row in session.query(CampaignAccount).filter_by(campaign_id=campaign.id)] == [valid.id]


def test_get_and_list_include_accounts_without_n_plus_one(client, account_env, pg_engine):
    session, campaigns, accounts = account_env
    campaign = _campaign(session)
    campaigns.append(campaign)
    legacy_campaign = _campaign(session)
    campaigns.append(legacy_campaign)
    accounts.extend([_account(session) for _ in range(2)])
    session.commit()
    assert client.put(
        f"/campaigns/{campaign.id}/accounts",
        json={"account_ids": [accounts[1].id, accounts[0].id]},
        headers=AUTH,
    ).status_code == 200

    detail = client.get(f"/campaigns/{campaign.id}", headers=AUTH)
    assert detail.status_code == 200
    assert detail.json()["account_ids"] == [accounts[1].id, accounts[0].id]
    assert detail.json()["sender_accounts"][0]["account_identifier"] == accounts[1].phone_number

    statements = []
    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)
    event.listen(pg_engine, "before_cursor_execute", capture)
    try:
        listed = client.get("/campaigns?limit=100", headers=AUTH)
    finally:
        event.remove(pg_engine, "before_cursor_execute", capture)
    assert listed.status_code == 200
    assert sum("campaign_accounts" in statement.lower() for statement in statements) == 1
    assert sum("from accounts" in statement.lower() for statement in statements) == 1
    assert sum(
        "campaign_recipients" in statement.lower()
        and "group by" in statement.lower()
        for statement in statements
    ) == 1
    legacy_item = next(
        item for item in listed.json()["items"] if item["id"] == legacy_campaign.id
    )
    assert legacy_item["account_ids"] == []
    assert legacy_item["sender_accounts"] == []


def test_campaign_delete_cascades_links(account_env):
    session, campaigns, accounts = account_env
    campaign = _campaign(session)
    account = _account(session)
    accounts.append(account)
    session.add(CampaignAccount(campaign_id=campaign.id, account_id=account.id))
    session.commit()
    campaign_id = campaign.id
    session.delete(campaign)
    session.commit()
    assert session.query(CampaignAccount).filter_by(campaign_id=campaign_id).count() == 0


def test_account_delete_cascades_links(account_env):
    session, campaigns, _accounts = account_env
    campaign = _campaign(session)
    campaigns.append(campaign)
    account = _account(session)
    session.add(CampaignAccount(campaign_id=campaign.id, account_id=account.id))
    session.commit()
    account_id = account.id
    session.delete(account)
    session.commit()
    assert session.query(CampaignAccount).filter_by(account_id=account_id).count() == 0


def test_create_from_import_with_accounts_and_omitted_legacy_behavior(client, account_env):
    session, campaigns, accounts = account_env
    accounts.extend([_account(session) for _ in range(2)])
    batch = ImportBatch(file_name=f"{uuid.uuid4().hex}.xlsx", status=ImportStatus.COMMITTED)
    session.add(batch)
    session.flush()
    contact = Contact(
        phone=f"09{uuid.uuid4().int % 10**9:09d}",
        source_import_id=batch.id,
        blacklisted=False,
    )
    session.add(contact)
    session.commit()
    base = {
        "import_batch_id": batch.id,
        "title": "Created with senders",
        "platform": "bale",
        "template_text": "hello",
    }
    response = client.post(
        "/campaigns/from-import",
        json={**base, "account_ids": [accounts[0].id, accounts[1].id, accounts[0].id]},
        headers=AUTH,
    )
    assert response.status_code == 200, response.text
    assert response.json()["account_ids"] == [accounts[0].id, accounts[1].id]
    campaigns.append(session.get(Campaign, response.json()["campaign_id"]))

    one = client.post(
        "/campaigns/from-import",
        json={**base, "account_ids": [accounts[0].id]},
        headers=AUTH,
    )
    assert one.status_code == 200, one.text
    assert one.json()["account_ids"] == [accounts[0].id]
    campaigns.append(session.get(Campaign, one.json()["campaign_id"]))

    legacy = client.post("/campaigns/from-import", json=base, headers=AUTH)
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["account_ids"] == []
    campaigns.append(session.get(Campaign, legacy.json()["campaign_id"]))

    created_ids = [campaign.id for campaign in campaigns]
    session.query(CampaignRecipient).filter(
        CampaignRecipient.campaign_id.in_(created_ids)
    ).delete(synchronize_session=False)
    session.query(CampaignAccount).filter(
        CampaignAccount.campaign_id.in_(created_ids)
    ).delete(synchronize_session=False)
    session.query(Campaign).filter(Campaign.id.in_(created_ids)).delete(
        synchronize_session=False
    )
    campaigns.clear()
    session.query(Contact).filter(Contact.id == contact.id).delete(synchronize_session=False)
    session.query(ImportBatch).filter(ImportBatch.id == batch.id).delete(synchronize_session=False)
    session.commit()


def test_create_validation_failure_rolls_back_campaign(client, account_env):
    session, _campaigns, _accounts = account_env
    batch = ImportBatch(file_name=f"{uuid.uuid4().hex}.xlsx", status=ImportStatus.COMMITTED)
    session.add(batch)
    session.flush()
    contact = Contact(
        phone=f"09{uuid.uuid4().int % 10**9:09d}",
        source_import_id=batch.id,
        blacklisted=False,
    )
    session.add(contact)
    session.commit()
    title = f"invalid-sender-{uuid.uuid4().hex}"

    response = client.post(
        "/campaigns/from-import",
        json={
            "import_batch_id": batch.id,
            "title": title,
            "platform": "bale",
            "template_text": "hello",
            "account_ids": [2_000_000_000],
        },
        headers=AUTH,
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "account_not_found"
    session.expire_all()
    assert session.query(Campaign).filter(Campaign.title == title).count() == 0

    session.query(Contact).filter(Contact.id == contact.id).delete(synchronize_session=False)
    session.query(ImportBatch).filter(ImportBatch.id == batch.id).delete(
        synchronize_session=False
    )
    session.commit()
