import uuid

import pytest

from core_engine.api.auth import get_current_user
from core_engine.main import app
from core_engine.models import (
    AccountSendSettings,
    Account,
    AccountStatus,
    AuditLog,
    CampaignRecipient,
    CampaignAccount,
    Campaign,
    CampaignStatus,
    ConsentStatus,
    Contact,
    ChannelSession,
    ImportBatch,
    PlatformType,
    Message,
    MessageAttempt,
    RenderedMessage,
    StagedQueueItem,
)
from core_engine.schemas.phase4 import PrepareMessagesRequest
from core_engine.services.phase4_prepare import prepare_campaign_messages


AUTH = {"Authorization": "Bearer fake_token"}


def _account(session, *, platform: PlatformType = PlatformType.RUBIKA, status=AccountStatus.ACTIVE) -> Account:
    account = Account(
        platform=platform,
        status=status,
        label=f"from-contacts-{uuid.uuid4().hex[:8]}",
        phone_number=f"09{uuid.uuid4().int % 10**9:09d}",
    )
    session.add(account)
    session.flush()
    return account


def _contact(
    session,
    *,
    first_name: str = "آزمایش",
    consent_status: str = ConsentStatus.ALLOWED.value,
    blacklisted: bool = False,
) -> Contact:
    contact = Contact(
        first_name=first_name,
        phone=f"+98{uuid.uuid4().int % 10**10:010d}",
        phone_e164=f"+98{uuid.uuid4().int % 10**10:010d}",
        consent_status=consent_status,
        blacklisted=blacklisted,
    )
    session.add(contact)
    session.flush()
    return contact


def _campaign_titles(session):
    return {t for (t,) in session.query(Contact).with_entities(Contact.id)}


def _make_request(
    *,
    contact_ids: list[int],
    title: str,
    platform: PlatformType,
    account_ids: list[int] | None,
    template_text: str = "سلام {{first_name}}، تست پیام",
    use_gpt: bool = False,
    include_products: bool = False,
):
    payload: dict[str, object] = {
        "contact_ids": contact_ids,
        "title": title,
        "platform": platform.value,
        "template_text": template_text,
        "use_gpt": use_gpt,
        "include_products": include_products,
    }
    # Existing backend expects either list or explicit null; tests use explicit list.
    payload["account_ids"] = account_ids
    return payload


def _cleanup_created_entities(
    session,
    *,
    account_id: int | None = None,
    contact_ids: list[int] | None = None,
    campaign_id: int | None = None,
    import_batch_id: int | None = None,
) -> None:
    """Best-effort teardown of rows created by these API tests.

    These tests create Accounts/Contacts/Campaigns directly in Postgres and
    previously relied on a globally clean DB, which breaks when suites run in
    a different order.
    """

    account_ids = [account_id] if account_id is not None else []
    contact_ids = list(contact_ids or [])
    campaign_ids = [campaign_id] if campaign_id is not None else []
    import_batch_ids = [import_batch_id] if import_batch_id is not None else []

    if campaign_ids:
        session.query(StagedQueueItem).filter(
            StagedQueueItem.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)
        session.query(RenderedMessage).filter(
            RenderedMessage.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)

        # CampaignRecipient.final_message_id references Message FK → delete
        # recipients first, then Message.
        session.query(CampaignRecipient).filter(
            CampaignRecipient.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)
        session.query(CampaignAccount).filter(
            CampaignAccount.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)

        if campaign_ids:
            session.query(MessageAttempt).join(Message).filter(
                Message.campaign_id.in_(campaign_ids)
            ).delete(synchronize_session=False)
            session.query(Message).filter(
                Message.campaign_id.in_(campaign_ids)
            ).delete(synchronize_session=False)

        session.query(AuditLog).filter(
            AuditLog.resource_type == "campaign",
            AuditLog.resource_id.in_([str(i) for i in campaign_ids]),
        ).delete(synchronize_session=False)
        session.query(Campaign).filter(Campaign.id.in_(campaign_ids)).delete(
            synchronize_session=False
        )

    if contact_ids:
        session.query(Contact).filter(Contact.id.in_(contact_ids)).delete(
            synchronize_session=False
        )

    if account_ids:
        session.query(ChannelSession).filter(
            ChannelSession.account_id.in_(account_ids)
        ).delete(synchronize_session=False)
        session.query(AccountSendSettings).filter(
            AccountSendSettings.account_id.in_(account_ids)
        ).delete(synchronize_session=False)
        session.query(AuditLog).filter(
            AuditLog.resource_type == "account",
            AuditLog.resource_id.in_([str(i) for i in account_ids]),
        ).delete(synchronize_session=False)
        session.query(Account).filter(Account.id.in_(account_ids)).delete(
            synchronize_session=False
        )

    if import_batch_ids:
        # Delete contact rows first, then import batches.
        session.query(ImportBatch).filter(
            ImportBatch.id.in_(import_batch_ids)
        ).delete(synchronize_session=False)

    session.commit()


def test_create_from_one_eligible_contact_creates_exactly_one_recipient(
    client, pg_session_factory
):
    session = pg_session_factory()
    account_id: int | None = None
    contact_ids: list[int] = []
    campaign_id: int | None = None
    try:
        account = _account(session)
        account_id = account.id
        contact = _contact(session, first_name="آزمایش 1", consent_status=ConsentStatus.ALLOWED.value)
        contact_ids = [contact.id]
        contact_source_import_id = contact.source_import_id
        before_contact_count = session.query(Contact).count()
        session.commit()

        title = f"cf-contact-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/campaigns/from-contacts",
            json=_make_request(
                contact_ids=[contact.id],
                title=title,
                platform=PlatformType.RUBIKA,
                account_ids=[account.id],
            ),
            headers=AUTH,
        )
        assert resp.status_code == 200, resp.text

        data = resp.json()
        assert data["contacts_attached_count"] == 1
        assert data["skipped_contacts_count"] == 0
        assert data["campaign_id"]
        assert data["account_ids"] == [account.id]
        assert len(data["sender_accounts"]) == 1

        campaign_id = data["campaign_id"]
        session.expire_all()
        recipients = session.query(CampaignRecipient).filter(CampaignRecipient.campaign_id == campaign_id).all()
        assert len(recipients) == 1
        assert recipients[0].contact_id == contact.id

        # No Contact mutation and no Contact row creation.
        refreshed = session.query(Contact).filter(Contact.id == contact.id).one()
        assert refreshed.source_import_id == contact_source_import_id
        assert session.query(Contact).count() == before_contact_count
    finally:
        _cleanup_created_entities(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            campaign_id=campaign_id,
        )
        session.close()


def test_create_from_multiple_contacts_attaches_only_eligible(
    client, pg_session_factory
):
    session = pg_session_factory()
    account_id: int | None = None
    contact_ids: list[int] = []
    campaign_id: int | None = None
    try:
        account = _account(session)
        account_id = account.id
        c1 = _contact(session, first_name="A", consent_status=ConsentStatus.ALLOWED.value)
        c2 = _contact(session, first_name="B", consent_status=ConsentStatus.BLOCKED.value)
        c3 = _contact(session, first_name="C", blacklisted=True)
        contact_ids = [c1.id, c2.id, c3.id]
        session.commit()

        title = f"cf-contact-mix-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/campaigns/from-contacts",
            json=_make_request(
                contact_ids=[c1.id, c2.id, c3.id],
                title=title,
                platform=PlatformType.RUBIKA,
                account_ids=[account.id],
            ),
            headers=AUTH,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["contacts_attached_count"] == 1
        assert data["skipped_contacts_count"] == 2

        campaign_id = data["campaign_id"]
        session.expire_all()
        recipients = session.query(CampaignRecipient).filter(CampaignRecipient.campaign_id == campaign_id).all()
        assert {r.contact_id for r in recipients} == {c1.id}
    finally:
        _cleanup_created_entities(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            campaign_id=campaign_id,
        )
        session.close()


def test_create_deduplicates_duplicate_contact_ids(
    client, pg_session_factory
):
    session = pg_session_factory()
    account_id: int | None = None
    contact_ids: list[int] = []
    campaign_id: int | None = None
    try:
        account = _account(session)
        account_id = account.id
        c1 = _contact(session, first_name="A", consent_status=ConsentStatus.ALLOWED.value)
        c2 = _contact(session, first_name="B", consent_status=ConsentStatus.ALLOWED.value)
        contact_ids = [c1.id, c2.id]
        session.commit()

        title = f"cf-contact-dedupe-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/campaigns/from-contacts",
            json=_make_request(
                contact_ids=[c1.id, c1.id, c2.id, c2.id],
                title=title,
                platform=PlatformType.RUBIKA,
                account_ids=[account.id],
            ),
            headers=AUTH,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["contacts_attached_count"] == 2

        campaign_id = data["campaign_id"]
        session.expire_all()
        recipients = session.query(CampaignRecipient).filter(CampaignRecipient.campaign_id == campaign_id).all()
        assert len(recipients) == 2
        assert {r.contact_id for r in recipients} == {c1.id, c2.id}
    finally:
        _cleanup_created_entities(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            campaign_id=campaign_id,
        )
        session.close()


def test_nonexistent_contact_ids_are_rejected(
    client, pg_session_factory
):
    session = pg_session_factory()
    account_id: int | None = None
    contact_ids: list[int] = []
    campaign_id: int | None = None
    try:
        account = _account(session)
        account_id = account.id
        c1 = _contact(session)
        contact_ids = [c1.id]
        session.commit()

        title = f"cf-contact-missing-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/campaigns/from-contacts",
            json=_make_request(
                contact_ids=[c1.id, 999999999],
                title=title,
                platform=PlatformType.RUBIKA,
                account_ids=[account.id],
            ),
            headers=AUTH,
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"]["code"] == "contact_not_found"
        session.expire_all()
        assert session.query(Campaign).filter(Campaign.title == title).count() == 0
    finally:
        _cleanup_created_entities(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            campaign_id=campaign_id,
        )
        session.close()


def test_zero_eligible_contacts_fails_without_writing_campaign(
    client, pg_session_factory
):
    session = pg_session_factory()
    account_id: int | None = None
    contact_ids: list[int] = []
    campaign_id: int | None = None
    try:
        account = _account(session)
        account_id = account.id
        c1 = _contact(session, consent_status=ConsentStatus.BLOCKED.value)
        c2 = _contact(session, blacklisted=True)
        contact_ids = [c1.id, c2.id]
        session.commit()

        title = f"cf-contact-zero-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/campaigns/from-contacts",
            json=_make_request(
                contact_ids=[c1.id, c2.id],
                title=title,
                platform=PlatformType.RUBIKA,
                account_ids=[account.id],
            ),
            headers=AUTH,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["code"] == "no_eligible_contacts"
        session.expire_all()
        assert session.query(Campaign).filter(Campaign.title == title).count() == 0
    finally:
        _cleanup_created_entities(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            campaign_id=campaign_id,
        )
        session.close()


def test_wrong_platform_sender_rejected(
    client, pg_session_factory
):
    session = pg_session_factory()
    account_id: int | None = None
    contact_ids: list[int] = []
    campaign_id: int | None = None
    try:
        account = _account(session, platform=PlatformType.BALE)
        account_id = account.id
        c1 = _contact(session)
        contact_ids = [c1.id]
        session.commit()

        title = f"cf-contact-wrong-platform-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/campaigns/from-contacts",
            json=_make_request(
                contact_ids=[c1.id],
                title=title,
                platform=PlatformType.RUBIKA,
                account_ids=[account.id],
            ),
            headers=AUTH,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["code"] == "account_platform_mismatch"
        session.expire_all()
        assert session.query(Campaign).filter(Campaign.title == title).count() == 0
    finally:
        _cleanup_created_entities(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            campaign_id=campaign_id,
        )
        session.close()


def test_inactive_sender_rejected(
    client, pg_session_factory
):
    session = pg_session_factory()
    account_id: int | None = None
    contact_ids: list[int] = []
    campaign_id: int | None = None
    try:
        account = _account(session, status=AccountStatus.RESTING)
        account_id = account.id
        c1 = _contact(session)
        contact_ids = [c1.id]
        session.commit()

        title = f"cf-contact-inactive-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/campaigns/from-contacts",
            json=_make_request(
                contact_ids=[c1.id],
                title=title,
                platform=PlatformType.RUBIKA,
                account_ids=[account.id],
            ),
            headers=AUTH,
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["code"] == "account_inactive"
        session.expire_all()
        assert session.query(Campaign).filter(Campaign.title == title).count() == 0
    finally:
        _cleanup_created_entities(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            campaign_id=campaign_id,
        )
        session.close()


def test_viewer_cannot_create_campaign_from_contacts(
    client, pg_session_factory
):
    session = pg_session_factory()
    account_id: int | None = None
    contact_ids: list[int] = []
    campaign_id: int | None = None
    try:
        account = _account(session)
        account_id = account.id
        c1 = _contact(session)
        contact_ids = [c1.id]
        session.commit()

        async def _viewer():
            return {"username": "viewer", "password": "viewer123", "role": "viewer"}

        app.dependency_overrides[get_current_user] = _viewer
        try:
            title = f"cf-contact-viewer-{uuid.uuid4().hex[:8]}"
            resp = client.post(
                "/campaigns/from-contacts",
                json=_make_request(
                    contact_ids=[c1.id],
                    title=title,
                    platform=PlatformType.RUBIKA,
                    account_ids=[account.id],
                ),
                headers=AUTH,
            )
            assert resp.status_code == 403, resp.text
            session.expire_all()
            assert session.query(Campaign).filter(Campaign.title == title).count() == 0
        finally:
            app.dependency_overrides.pop(get_current_user, None)
    finally:
        _cleanup_created_entities(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            campaign_id=campaign_id,
        )
        session.close()


def test_prepare_pipeline_creates_messages_with_persisted_manual_sender_account(
    client, pg_session_factory, monkeypatch
):
    session = pg_session_factory()
    account_id: int | None = None
    contact_ids: list[int] = []
    campaign_id: int | None = None
    try:
        account = _account(session, platform=PlatformType.RUBIKA)
        account_id = account.id
        c1 = _contact(session, first_name="آزمایش", consent_status=ConsentStatus.ALLOWED.value)
        contact_ids = [c1.id]
        session.commit()

        title = f"cf-contact-prepare-{uuid.uuid4().hex[:8]}"
        resp = client.post(
            "/campaigns/from-contacts",
            json=_make_request(
                contact_ids=[c1.id],
                title=title,
                platform=PlatformType.RUBIKA,
                account_ids=[account.id],
            ),
            headers=AUTH,
        )
        assert resp.status_code == 200, resp.text
        campaign_id = resp.json()["campaign_id"]

        # Ensure GPT/products off doesn't trigger providers.
        import core_engine.services.phase4_prepare as phase4_prepare

        monkeypatch.setattr(
            phase4_prepare,
            "generate_validated_pool",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("GPT provider called")),
        )
        monkeypatch.setattr(
            phase4_prepare,
            "fetch_current_advertising_products",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("Product feed provider called")
            ),
        )

        prepare_campaign_messages(
            session,
            campaign_id,
            PrepareMessagesRequest(force_mock_output=False),
        )

        # Persisted Message.account_id must follow manual campaign sender selection.
        session.expire_all()
        msg = session.query(Message).filter(Message.campaign_id == campaign_id).one()
        assert msg.account_id == account.id
    finally:
        _cleanup_created_entities(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            campaign_id=campaign_id,
        )
        session.close()


def test_from_import_regression_still_works(
    client, pg_session_factory
):
    from core_engine.models import ImportBatch, ImportStatus
    session = pg_session_factory()
    account_id: int | None = None
    contact_ids: list[int] = []
    campaign_id: int | None = None
    import_batch_id: int | None = None
    try:
        account = _account(session)
        account_id = account.id
        batch = ImportBatch(file_name=f"{uuid.uuid4().hex}.xlsx", status=ImportStatus.COMMITTED)
        session.add(batch)
        session.flush()
        contact = _contact(session)
        contact_ids = [contact.id]
        contact.source_import_id = batch.id
        session.add(contact)
        session.commit()
        import_batch_id = batch.id

        resp = client.post(
            "/campaigns/from-import",
            json={
                "import_batch_id": batch.id,
                "title": f"cf-import-reg-{uuid.uuid4().hex[:8]}",
                "platform": PlatformType.RUBIKA.value,
                "template_text": "سلام {{first_name}}",
                "use_gpt": False,
                "include_products": False,
                "account_ids": [account.id],
            },
            headers=AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["account_ids"] == [account.id]
        # Best-effort cleanup: campaign id is returned by the backend.
        campaign_id = resp.json().get("campaign_id")
    finally:
        _cleanup_created_entities(
            session,
            account_id=account_id,
            contact_ids=contact_ids,
            campaign_id=campaign_id,
            import_batch_id=import_batch_id,
        )
        session.close()

