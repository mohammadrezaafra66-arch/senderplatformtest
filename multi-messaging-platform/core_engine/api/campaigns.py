"""API مدیریت کمپین‌های ارسال."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from core_engine.api.schemas import (
    CampaignAccountsResponse,
    CampaignAccountsUpdateRequest,
    CampaignDetailResponse,
    CampaignFromImportRequest,
    CampaignFromImportResponse,
    CampaignListItemResponse,
    CampaignRecipientsListResponse,
    CampaignsListResponse,
    CampaignStartResponse,
    CampaignStatsData,
    CampaignStopResponse,
    SenderAccountResponse,
)
from core_engine.database import get_db
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    CampaignStatus,
    ConsentStatus,
    Contact,
    ImportBatch,
    ImportStatus,
    PlatformType,
    RenderStatus,
    RoleType,
    SendStatus,
)
from core_engine.services.audit_service import record_audit
from core_engine.services.campaign_control import start_campaign, stop_campaign
from core_engine.services.campaign_recipients import (
    CSV_EXPORT_MAX_ROWS,
    build_recipients_csv_bytes,
    export_filename,
    fetch_campaign_recipient_rows,
    get_campaign_or_404,
    recipient_to_response,
)
from core_engine.services.dashboard import get_campaign_stats
from core_engine.services.rbac import requires_role

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _sender_accounts(campaign: Campaign) -> list[SenderAccountResponse]:
    return [
        SenderAccountResponse(
            account_id=link.account_id,
            label=link.account.label,
            account_identifier=link.account.phone_number,
            platform=link.account.platform,
            status=link.account.status,
            priority=link.priority,
            weight=link.weight,
            enabled=link.enabled,
        )
        for link in sorted(campaign.campaign_accounts, key=lambda item: item.priority)
    ]


def _account_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


def _validate_accounts(
    db: Session, account_ids: list[int], platform: PlatformType
) -> list[Account]:
    if not account_ids:
        return []
    accounts = db.query(Account).filter(Account.id.in_(account_ids)).all()
    by_id = {account.id: account for account in accounts}
    for account_id in account_ids:
        account = by_id.get(account_id)
        if account is None:
            raise _account_error(404, "account_not_found", f"Account {account_id} was not found.")
        if account.status != AccountStatus.ACTIVE:
            raise _account_error(400, "account_inactive", f"Account {account_id} is not active.")
        if account.platform != platform:
            raise _account_error(
                400,
                "account_platform_mismatch",
                f"Account {account_id} does not match campaign platform {platform.value}.",
            )
    return [by_id[account_id] for account_id in account_ids]


def _sync_campaign_accounts(
    db: Session,
    campaign: Campaign,
    account_ids: list[int],
    validated_accounts: list[Account] | None = None,
) -> None:
    if validated_accounts is None:
        _validate_accounts(db, account_ids, campaign.platform)

    existing_links = {
        link.account_id: link
        for link in db.query(CampaignAccount)
        .filter(CampaignAccount.campaign_id == campaign.id)
        .all()
    }
    requested_ids = set(account_ids)
    for account_id, link in existing_links.items():
        if account_id not in requested_ids:
            db.delete(link)

    for priority, account_id in enumerate(account_ids, start=1):
        link = existing_links.get(account_id)
        if link is None:
            db.add(CampaignAccount(
                campaign_id=campaign.id,
                account_id=account_id,
                priority=priority,
                weight=1,
                enabled=True,
            ))
        else:
            link.priority = priority


@router.get("", response_model=CampaignsListResponse)
def list_campaigns(
    limit: int = 10,
    offset: int = 0,
    status: str | None = None,
    platform: PlatformType | None = None,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))] = None,
):
    """لیست کمپین‌ها با pagination و فیلتر."""
    query = db.query(Campaign).options(
        selectinload(Campaign.campaign_accounts).selectinload(CampaignAccount.account)
    )

    if status:
        query = query.filter(Campaign.status == status)
    if platform:
        query = query.filter(Campaign.platform == platform)

    total_count = query.count()

    campaigns = query.order_by(Campaign.created_at.desc()).limit(limit).offset(offset).all()

    campaign_ids = [campaign.id for campaign in campaigns]
    recipient_counts = (
        dict(
            db.query(CampaignRecipient.campaign_id, func.count(CampaignRecipient.id))
            .filter(CampaignRecipient.campaign_id.in_(campaign_ids))
            .group_by(CampaignRecipient.campaign_id)
            .all()
        )
        if campaign_ids
        else {}
    )

    items = []
    for campaign in campaigns:
        senders = _sender_accounts(campaign)
        items.append(CampaignListItemResponse(
            id=campaign.id,
            name=campaign.name,
            title=campaign.title,
            platform=campaign.platform,
            status=campaign.status,
            created_at=campaign.created_at,
            total_recipients=recipient_counts.get(campaign.id, 0),
            account_ids=[sender.account_id for sender in senders],
            sender_accounts=senders,
        ))

    return CampaignsListResponse(
        items=items,
        total_count=total_count,
        limit=limit,
        offset=offset,
    )


@router.get("/{campaign_id}", response_model=CampaignDetailResponse)
def get_campaign_detail(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))] = None,
):
    """جزئیات یک کمپین با stats."""
    campaign = (
        db.query(Campaign)
        .options(
            selectinload(Campaign.campaign_accounts).selectinload(CampaignAccount.account)
        )
        .filter(Campaign.id == campaign_id)
        .first()
    )
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    stats_dict = get_campaign_stats(db, campaign_id)
    stats = CampaignStatsData(**stats_dict)

    senders = _sender_accounts(campaign)
    return CampaignDetailResponse(
        id=campaign.id,
        name=campaign.name,
        title=campaign.title,
        channel=campaign.channel,
        platform=campaign.platform,
        status=campaign.status,
        template_text=campaign.template_text,
        use_gpt=campaign.use_gpt,
        include_products=campaign.include_products,
        intent=campaign.intent,
        message_goal=campaign.message_goal,
        max_contacts=campaign.max_contacts,
        daily_limit=campaign.daily_limit,
        schedule_start_at=campaign.schedule_start_at,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        stats=stats,
        account_ids=[sender.account_id for sender in senders],
        sender_accounts=senders,
    )


@router.put("/{campaign_id}/accounts", response_model=CampaignAccountsResponse)
def update_campaign_accounts(
    campaign_id: int,
    payload: CampaignAccountsUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ],
):
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    try:
        _sync_campaign_accounts(db, campaign, payload.account_ids)
        db.commit()
        campaign = (
            db.query(Campaign)
            .options(
                selectinload(Campaign.campaign_accounts).selectinload(CampaignAccount.account)
            )
            .filter(Campaign.id == campaign_id)
            .one()
        )
        senders = _sender_accounts(campaign)
        return CampaignAccountsResponse(
            campaign_id=campaign.id,
            account_ids=[sender.account_id for sender in senders],
            sender_accounts=senders,
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="Failed to update campaign accounts."
        ) from exc


@router.post("/{campaign_id}/start", response_model=CampaignStartResponse)
async def start_campaign_endpoint(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))] = None,
):
    """شروع کمپین: RUNNING در DB + حذف pause از Redis + push اولیه staged items."""
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    try:
        result = await start_campaign(db, campaign)
        record_audit(
            db,
            current_user["username"],
            "start_campaign",
            "campaign",
            str(campaign.id),
            {"bridge_result": result.get("bridge_result")},
        )
        db.commit()
        db.refresh(campaign)
        return CampaignStartResponse(**result)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to start campaign.") from exc


@router.post("/{campaign_id}/stop", response_model=CampaignStopResponse)
async def stop_campaign_endpoint(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))] = None,
):
    """توقف کمپین: PAUSED در DB + set کردن campaign pause در Redis."""
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    try:
        result = await stop_campaign(db, campaign)
        record_audit(
            db,
            current_user["username"],
            "stop_campaign",
            "campaign",
            str(campaign.id),
            {"paused_in_redis": result["paused_in_redis"]},
        )
        db.commit()
        db.refresh(campaign)
        return CampaignStopResponse(**result)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to stop campaign.") from exc


@router.get("/{campaign_id}/recipients/export")
def export_campaign_recipients_csv(
    campaign_id: int,
    send_status: str | None = None,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str],
        Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR, RoleType.VIEWER)),
    ] = None,
):
    """دانلود CSV گیرندگان کمپین (message logs)."""
    get_campaign_or_404(db, campaign_id)
    rows, total_count = fetch_campaign_recipient_rows(
        db,
        campaign_id,
        send_status=send_status,
        limit=CSV_EXPORT_MAX_ROWS,
        offset=0,
    )
    if total_count > CSV_EXPORT_MAX_ROWS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Export limit exceeded ({total_count} rows). "
                f"Maximum export size is {CSV_EXPORT_MAX_ROWS} rows. "
                "Apply send_status filter to narrow results."
            ),
        )

    content = build_recipients_csv_bytes(rows)
    filename = export_filename(campaign_id)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{campaign_id}/recipients", response_model=CampaignRecipientsListResponse)
def list_campaign_recipients(
    campaign_id: int,
    limit: int = 50,
    offset: int = 0,
    send_status: str | None = None,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str],
        Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR, RoleType.VIEWER)),
    ] = None,
):
    """Message logs: لیست گیرندگان کمپین با وضعیت render/send."""
    rows, total_count = fetch_campaign_recipient_rows(
        db,
        campaign_id,
        send_status=send_status,
        limit=limit,
        offset=offset,
    )

    items = [recipient_to_response(recipient, contact) for recipient, contact in rows]

    return CampaignRecipientsListResponse(
        campaign_id=campaign_id,
        items=items,
        total_count=total_count,
        limit=limit,
        offset=offset,
    )


@router.post("/from-import", response_model=CampaignFromImportResponse)
def create_campaign_from_import(
    payload: CampaignFromImportRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))],
):
    import_batch = (
        db.query(ImportBatch)
        .filter(ImportBatch.id == payload.import_batch_id)
        .first()
    )
    if not import_batch:
        raise HTTPException(status_code=404, detail="Import batch not found.")

    if import_batch.status != ImportStatus.COMMITTED:
        raise HTTPException(
            status_code=400,
            detail="Import batch is not in committed status.",
        )

    all_import_contacts = (
        db.query(Contact)
        .filter(Contact.source_import_id == payload.import_batch_id)
        .all()
    )

    eligible_contacts = [
        contact
        for contact in all_import_contacts
        if not contact.blacklisted
        and contact.consent_status != ConsentStatus.BLOCKED.value
    ]

    if not eligible_contacts:
        raise HTTPException(
            status_code=400,
            detail="No eligible contacts found for this import.",
        )

    skipped_contacts_count = len(all_import_contacts) - len(eligible_contacts)

    try:
        validated_accounts = None
        if payload.account_ids is not None:
            validated_accounts = _validate_accounts(
                db, payload.account_ids, payload.platform
            )
        campaign = Campaign(
            name=payload.title,
            channel=payload.platform.value,
            title=payload.title,
            platform=payload.platform,
            status=CampaignStatus.DRAFT.value,
            template_text=payload.template_text,
            use_gpt=payload.use_gpt,
            include_products=payload.include_products,
        )
        db.add(campaign)
        db.flush()

        if payload.account_ids is not None:
            _sync_campaign_accounts(
                db,
                campaign,
                payload.account_ids,
                validated_accounts=validated_accounts,
            )

        contacts_attached_count = 0
        for contact in eligible_contacts:
            existing_recipient = (
                db.query(CampaignRecipient)
                .filter(
                    CampaignRecipient.campaign_id == campaign.id,
                    CampaignRecipient.contact_id == contact.id,
                )
                .first()
            )
            if existing_recipient:
                continue

            recipient = CampaignRecipient(
                campaign_id=campaign.id,
                contact_id=contact.id,
                render_status=RenderStatus.PENDING,
                send_status=SendStatus.PENDING,
            )
            db.add(recipient)
            contacts_attached_count += 1

        db.commit()
        db.refresh(campaign)

        record_audit(
            db,
            current_user["username"],
            "create_campaign",
            "campaign",
            str(campaign.id),
            {
                "import_batch_id": payload.import_batch_id,
                "contacts_attached_count": contacts_attached_count,
                "skipped_contacts_count": skipped_contacts_count,
            },
        )
        db.commit()

        campaign = (
            db.query(Campaign)
            .options(
                selectinload(Campaign.campaign_accounts).selectinload(CampaignAccount.account)
            )
            .filter(Campaign.id == campaign.id)
            .one()
        )
        senders = _sender_accounts(campaign)
        return CampaignFromImportResponse(
            status="draft_created",
            campaign_id=campaign.id,
            import_batch_id=payload.import_batch_id,
            contacts_attached_count=contacts_attached_count,
            skipped_contacts_count=skipped_contacts_count,
            message="Campaign draft created from import successfully",
            account_ids=[sender.account_id for sender in senders],
            sender_accounts=senders,
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Failed to create campaign from import.",
        ) from exc
