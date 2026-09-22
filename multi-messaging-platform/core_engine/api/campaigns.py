"""API مدیریت کمپین‌های ارسال."""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload
import uuid

from core_engine.api.schemas import (
    ArchiveActionRequest,
    ArchiveActionResponse,
    CampaignAccountsResponse,
    CampaignAccountsUpdateRequest,
    CampaignAutoPrepareSummary,
    AudiencePreviewRequest,
    AudiencePreviewResponse,
    CampaignFromTagsRequest,
    CampaignFromTagsResponse,
    CampaignFromContactsRequest,
    CampaignFromContactsResponse,
    CampaignDetailResponse,
    CampaignFromImportRequest,
    CampaignFromImportResponse,
    CampaignListItemResponse,
    CampaignPreflightResponse,
    CampaignPrepareRequest,
    CampaignPrepareResponse,
    CampaignRecipientDetailResponse,
    CampaignRecipientsListResponse,
    CampaignRenderPreviewRequest,
    CampaignsListResponse,
    CampaignStartRequest,
    CampaignStartResponse,
    CampaignStatsData,
    CampaignStopResponse,
    GptPreviewRequest,
    SenderAccountResponse,
)
from core_engine.database import get_db
from core_engine.services.contact_import import resolve_contacts_for_import_batch
from core_engine.services.contact_tags import (
    filter_eligible,
    read_contact_tags,
    resolve_tag_audience,
)
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
from core_engine.services.campaign_sender_assignment import (
    ensure_automatic_campaign_sender_assignments,
    resync_campaign_prepared_senders,
)
from core_engine.services.contact_delete import is_contact_deleted
from core_engine.services.campaign_control import start_campaign, stop_campaign
from core_engine.services.campaign_recipients import (
    CSV_EXPORT_MAX_ROWS,
    build_recipients_csv_bytes,
    export_filename,
    fetch_campaign_recipient_rows,
    fetch_committed_render_samples,
    fetch_recipient_detail,
    get_campaign_or_404,
    recipient_to_response,
)
from core_engine.services.dashboard import get_campaign_stats
from core_engine.services.rbac import requires_role

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _auto_prepare_summary(db: Session, campaign_id: int, *, trigger: str):
    from core_engine.services.campaign_auto_prepare import auto_prepare_after_mutation

    result = auto_prepare_after_mutation(db, campaign_id, trigger=trigger)
    from core_engine.api.schemas import CampaignAutoPrepareSummary

    payload = result.to_dict()
    return CampaignAutoPrepareSummary(
        attempted=bool(payload.get("attempted")),
        prepared=bool(payload.get("prepared")),
        skipped=bool(payload.get("skipped")),
        skip_reason=payload.get("skip_reason"),
        blockers=list(payload.get("blockers") or []),
        error_code=payload.get("error_code"),
        error_message=payload.get("error_message"),
        ready_count=payload.get("ready_count"),
        staged_count=payload.get("staged_count"),
    )


def _create_campaign_draft_from_eligible_contacts(
    db: Session,
    current_user: dict[str, str],
    *,
    title: str,
    platform: PlatformType,
    template_text: str,
    use_gpt: bool,
    include_products: bool,
    account_ids: list[int] | None,
    eligible_contacts: list[Contact],
    skipped_contacts_count: int,
    audit_source: str,
    audit_extra_details: dict[str, object] | None = None,
) -> tuple[Campaign, int, list[SenderAccountResponse], Any, Any]:
    """Shared helper for:
    - Campaign construction (DRAFT)
    - Manual sender account validation/sync
    - Automatic stable CampaignAccount assignment for Rubika when account_ids is empty
    - CampaignRecipient creation from eligible Contact rows
    - Audit logging
    """

    manual_account_ids = list(account_ids or [])
    validated_accounts = None
    if manual_account_ids:
        validated_accounts = _validate_accounts(db, manual_account_ids, platform)

    campaign = Campaign(
        name=title,
        channel=platform.value,
        title=title,
        platform=platform,
        status=CampaignStatus.DRAFT.value,
        template_text=template_text,
        use_gpt=use_gpt,
        include_products=include_products,
    )
    db.add(campaign)
    db.flush()

    if manual_account_ids:
        _sync_campaign_accounts(
            db,
            campaign,
            manual_account_ids,
            validated_accounts=validated_accounts,
        )

    contacts_attached_count = 0
    for contact in eligible_contacts:
        # Defensive check: keep semantics consistent with from-import.
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

        db.add(
            CampaignRecipient(
                campaign_id=campaign.id,
                contact_id=contact.id,
                render_status=RenderStatus.PENDING,
                send_status=SendStatus.PENDING,
            )
        )
        contacts_attached_count += 1

    sender_assignment = None
    if not manual_account_ids and platform == PlatformType.RUBIKA:
        assigned = ensure_automatic_campaign_sender_assignments(db, campaign)
        sender_assignment = assigned.to_dict()
        record_audit(
            db,
            current_user["username"],
            "assign_campaign_senders",
            "campaign",
            str(campaign.id),
            {
                "source": audit_source,
                "mode": "automatic",
                "eligible_accounts": assigned.eligible_accounts,
                "created_assignments": assigned.created_assignments,
                "existing_assignments": assigned.existing_assignments,
                "reason": assigned.reason,
            },
        )

    record_audit(
        db,
        current_user["username"],
        "create_campaign",
        "campaign",
        str(campaign.id),
        {
            "source": audit_source,
            **(audit_extra_details or {}),
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
    senders = _sender_accounts(campaign, db)
    automatic_without_execution_ready = bool(
        sender_assignment
        and platform == PlatformType.RUBIKA
        and not any(sender.campaign_eligible for sender in senders)
    )
    if automatic_without_execution_ready:
        from core_engine.api.schemas import CampaignAutoPrepareSummary

        auto_prepare = CampaignAutoPrepareSummary(
            attempted=False,
            prepared=False,
            skipped=True,
            skip_reason="assignment_without_execution_ready_senders",
        )
    else:
        auto_prepare = _auto_prepare_summary(db, campaign.id, trigger="create")
    return campaign, contacts_attached_count, senders, auto_prepare, sender_assignment


def _sender_accounts(campaign: Campaign, db: Session | None = None) -> list[SenderAccountResponse]:
    links = sorted(campaign.campaign_accounts, key=lambda item: item.priority)
    accounts = [link.account for link in links if link.account is not None]
    elig_by_id: dict[int, Any] = {}
    from core_engine.services.campaign_sender_eligibility import (
        evaluate_campaign_sender_eligibility_batch,
        resolve_display_identity,
    )

    if db is not None and accounts:
        elig_by_id = evaluate_campaign_sender_eligibility_batch(
            db,
            accounts,
            campaign=campaign,
            assigned_ids={int(a.id) for a in accounts},
        )
    out: list[SenderAccountResponse] = []
    for link in links:
        account = link.account
        elig = elig_by_id.get(int(link.account_id)) if account is not None else None
        display = (
            elig.display_identity
            if elig is not None
            else resolve_display_identity(
                account_id=int(link.account_id),
                phone_number=account.phone_number if account else None,
                label=account.label if account else None,
            )
        )
        out.append(
            SenderAccountResponse(
                account_id=link.account_id,
                label=account.label if account else None,
                account_identifier=account.phone_number if account else None,
                display_identity=display,
                platform=account.platform if account else campaign.platform,
                status=account.status if account else AccountStatus.REQUIRES_LOGIN,
                priority=link.priority,
                weight=link.weight,
                enabled=link.enabled,
                runtime_status=elig.runtime_status if elig else None,
                runtime_status_label=elig.runtime_status_label if elig else None,
                campaign_eligible=elig.campaign_eligible if elig else None,
                blocker_code=elig.blocker_code if elig else None,
                blocker_label=elig.blocker_label if elig else None,
                auth_ready=elig.auth_ready if elig else None,
                worker_ready=elig.worker_ready if elig else None,
                dispatch_ready=elig.dispatch_ready if elig else None,
            )
        )
    return out


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
    db.flush()
    resync_campaign_prepared_senders(db, campaign)


@router.get("", response_model=CampaignsListResponse)
def list_campaigns(
    limit: int = 10,
    offset: int = 0,
    status: str | None = None,
    platform: PlatformType | None = None,
    archived: bool = False,
    q: str | None = None,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))] = None,
):
    """لیست کمپین‌ها با pagination و فیلتر.

    By default excludes archived campaigns. Pass archived=true for Archive list.
    """
    query = db.query(Campaign).options(
        selectinload(Campaign.campaign_accounts).selectinload(CampaignAccount.account)
    )

    if archived:
        query = query.filter(Campaign.archived_at.isnot(None))
    else:
        query = query.filter(Campaign.archived_at.is_(None))
    if status:
        query = query.filter(Campaign.status == status)
    if platform:
        query = query.filter(Campaign.platform == platform)
    if q:
        needle = f"%{q.strip()}%"
        try:
            as_id = int(q.strip())
        except ValueError:
            as_id = None
        if as_id is not None:
            query = query.filter(
                (Campaign.id == as_id)
                | (Campaign.name.ilike(needle))
                | (Campaign.title.ilike(needle))
            )
        else:
            query = query.filter(
                (Campaign.name.ilike(needle)) | (Campaign.title.ilike(needle))
            )

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
        senders = _sender_accounts(campaign, db)
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
            archived_at=campaign.archived_at,
            archived_by=campaign.archived_by,
        ))

    return CampaignsListResponse(
        items=items,
        total_count=total_count,
        limit=limit,
        offset=offset,
    )


@router.get("/product-feed/status")
def product_feed_status_endpoint(
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    """Operator preflight for advertising products. Never exposes API tokens."""
    from core_engine.services.product_feed.service import product_feed_status

    return product_feed_status()


@router.get("/gpt-status")
def gpt_status_endpoint(
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    """Boolean GPT readiness. Never exposes API keys or authorization headers."""
    from core_engine.services.message_variation.service import gpt_status

    return gpt_status()


@router.post("/gpt-preview")
def gpt_preview_endpoint(
    payload: GptPreviewRequest,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    """Generate a bounded GPT preview using the same provider/validator as prepare."""
    from core_engine.services.message_variation.errors import GptVariationError
    from core_engine.services.message_variation.service import build_gpt_preview

    try:
        return build_gpt_preview(
            template_text=payload.template_text,
            include_products=payload.include_products,
            requested_count=payload.requested_count,
            apply_rate_guard=True,
        )
    except GptVariationError as exc:
        raise HTTPException(status_code=400, detail=exc.http_detail()) from exc


@router.post("/render-preview")
def campaign_render_preview_endpoint(
    payload: CampaignRenderPreviewRequest,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    """Sample composition preview using the same final-render pipeline as prepare.

    Not committed. Does not accept secrets or client-authored final_text.
    """
    from core_engine.services.campaign_render import build_campaign_render_preview
    from core_engine.services.message_variation.errors import GptVariationError
    from core_engine.services.product_feed.errors import ProductFeedError

    try:
        return build_campaign_render_preview(
            template_text=payload.template_text,
            use_gpt=payload.use_gpt,
            include_products=payload.include_products,
            preview_count=payload.preview_count,
            preview_variables=payload.preview_variables,
            apply_gpt_rate_guard=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GptVariationError as exc:
        raise HTTPException(status_code=400, detail=exc.http_detail()) from exc
    except ProductFeedError as exc:
        raise HTTPException(status_code=400, detail=exc.http_detail()) from exc


@router.get("/{campaign_id}/preflight", response_model=CampaignPreflightResponse)
async def get_campaign_preflight(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str],
        Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR, RoleType.VIEWER)),
    ] = None,
):
    """Read-only campaign capacity / safety preflight. No mutation."""
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    from core_engine.services.campaign_preflight import (
        CAMPAIGN_CAPACITY_UNKNOWN,
        CAMPAIGN_DEPENDENCY_ERROR,
        evaluate_campaign_send_preflight,
    )

    result = await evaluate_campaign_send_preflight(db, campaign_id)
    if result.code == CAMPAIGN_DEPENDENCY_ERROR:
        raise HTTPException(
            status_code=503,
            detail={"code": result.code, "message": result.message},
        )
    if result.code == CAMPAIGN_CAPACITY_UNKNOWN and not result.redis_ok:
        # Still return structured body so the UI can show the blocker; 200 with
        # allowed_to_start=false is the planning contract (start remains 503).
        pass
    return CampaignPreflightResponse(**result.to_dict())


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

    senders = _sender_accounts(campaign, db)
    committed_renders, latest_batch = fetch_committed_render_samples(db, campaign_id)
    from core_engine.services.campaign_render import RENDER_VERSION

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
        latest_render_batch_id=latest_batch,
        render_version=RENDER_VERSION if committed_renders else None,
        committed_renders=committed_renders,
        archived_at=campaign.archived_at,
        archived_by=campaign.archived_by,
        archive_reason=campaign.archive_reason,
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
        if not payload.account_ids and campaign.platform == PlatformType.RUBIKA:
            ensure_automatic_campaign_sender_assignments(db, campaign)
            db.flush()
            resync_campaign_prepared_senders(db, campaign)
        else:
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
        senders = _sender_accounts(campaign, db)
        from core_engine.services.campaign_readiness_contract import assignment_eligibility_warnings
        from core_engine.services.campaign_sender_eligibility import (
            evaluate_campaign_sender_eligibility_batch,
        )

        accounts = [link.account for link in campaign.campaign_accounts if link.account]
        elig = evaluate_campaign_sender_eligibility_batch(
            db,
            accounts,
            campaign=campaign,
            assigned_ids={int(a.id) for a in accounts},
        )
        warnings = assignment_eligibility_warnings(list(elig.values()))
        auto_prepare = CampaignAutoPrepareSummary(
            attempted=False,
            prepared=False,
            skipped=True,
            skip_reason="accounts_update_does_not_prepare",
        )
        return CampaignAccountsResponse(
            campaign_id=campaign.id,
            account_ids=[sender.account_id for sender in senders],
            sender_accounts=senders,
            assignment_warnings=warnings,
            auto_prepare=auto_prepare,
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="Failed to update campaign accounts."
        ) from exc


@router.post("/{campaign_id}/archive", response_model=ArchiveActionResponse)
def archive_campaign_endpoint(
    campaign_id: int,
    payload: ArchiveActionRequest | None = None,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    """Soft-archive a campaign and stop future undispatched sends. Idempotent."""
    from core_engine.services.archive import archive_campaign

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    reason = payload.reason if payload else None
    result = archive_campaign(
        db,
        campaign,
        actor=current_user["username"],
        reason=reason,
    )
    db.commit()
    return ArchiveActionResponse(
        status="archived",
        entity_type=result.entity_type,
        entity_id=result.entity_id,
        already_archived=result.already_archived,
        archived_at=result.archived_at,
        previous_status=result.previous_status,
        queued_items_cancelled=result.queued_items_cancelled,
        pending_recipients_stopped=result.pending_recipients_stopped,
        already_sent_count=result.already_sent_count,
        in_flight_count=result.in_flight_count,
        message=(
            "Campaign already archived."
            if result.already_archived
            else "Campaign archived; future sends stopped."
        ),
        details=result.details,
    )


@router.post("/{campaign_id}/restore", response_model=ArchiveActionResponse)
def restore_campaign_endpoint(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    """Restore archived campaign. Never auto-starts or auto-sends."""
    from core_engine.services.archive import restore_campaign

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    result = restore_campaign(db, campaign, actor=current_user["username"])
    db.commit()
    return ArchiveActionResponse(
        status="restored",
        entity_type=result.entity_type,
        entity_id=result.entity_id,
        already_active=result.already_active,
        restored_at=result.restored_at,
        previous_status=result.previous_status,
        message=(
            "Campaign already active."
            if result.already_active
            else "Campaign restored; explicit Start required."
        ),
        details=result.details,
    )


@router.post("/{campaign_id}/start", response_model=CampaignStartResponse)
async def start_campaign_endpoint(
    campaign_id: int,
    payload: CampaignStartRequest = Body(default_factory=CampaignStartRequest),
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))] = None,
    x_request_id: Annotated[str | None, Header()] = None,
):
    """شروع کمپین: RUNNING در DB + حذف pause از Redis + push اولیه staged items."""
    request_id = (x_request_id or "").strip() or str(uuid.uuid4())
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    from core_engine.services.archive import require_campaign_not_archived

    require_campaign_not_archived(campaign)

    try:
        result = await start_campaign(
            db,
            campaign,
            confirm_controlled_production=bool(payload.confirm_controlled_production),
            request_id=request_id,
        )
        record_audit(
            db,
            current_user["username"],
            "start_campaign",
            "campaign",
            str(campaign.id),
            {
                "bridge_result": result.get("bridge_result"),
                "request_id": request_id,
                "controlled_confirmation_received": bool(
                    payload.confirm_controlled_production
                ),
            },
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


_PREPARE_ALLOWED_STATUSES = frozenset(
    {
        CampaignStatus.DRAFT.value,
        CampaignStatus.PREPARED.value,
        CampaignStatus.PAUSED.value,
    }
)


@router.post("/{campaign_id}/prepare", response_model=CampaignPrepareResponse)
def prepare_campaign_endpoint(
    campaign_id: int,
    payload: CampaignPrepareRequest = CampaignPrepareRequest(),
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    """Render final messages and stage them for sending. Does not start delivery."""
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    if campaign.status not in _PREPARE_ALLOWED_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CAMPAIGN_STATUS_NOT_PREPAREABLE",
                "message": "این کمپین در وضعیت فعلی قابل آماده‌سازی نیست.",
            },
        )

    from core_engine.services.campaign_auto_prepare import try_auto_prepare_campaign

    try:
        if payload.force_mock_output or payload.limit is not None:
            from core_engine.schemas.phase4 import PrepareMessagesRequest
            from core_engine.services.phase4_prepare import prepare_campaign_messages

            result = prepare_campaign_messages(
                db,
                campaign_id,
                PrepareMessagesRequest(
                    force_mock_output=payload.force_mock_output,
                    limit=payload.limit,
                ),
            )
        else:
            auto_result = try_auto_prepare_campaign(
                db, campaign_id, trigger="manual_prepare", force=True
            )
            if auto_result.error_code:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": auto_result.error_code,
                        "message": auto_result.error_message or "Prepare failed.",
                    },
                )
            if auto_result.blockers and not auto_result.prepared:
                raise HTTPException(status_code=400, detail=auto_result.blockers[0])
            result = auto_result.prepare_result
            if result is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "PREPARE_NOOP",
                        "message": auto_result.skip_reason or "Prepare did not produce messages.",
                    },
                )
        record_audit(
            db,
            current_user["username"],
            "prepare_campaign",
            "campaign",
            str(campaign.id),
            {
                "staged_count": result.staged_count,
                "ready_count": result.ready_count,
                "real_gpt_called": result.real_gpt_called,
            },
        )
        db.commit()
        return CampaignPrepareResponse(
            campaign_id=result.campaign_id,
            total_contacts=result.total_contacts,
            allowed_contacts=result.allowed_contacts,
            skipped_contacts=result.skipped_contacts,
            staged_count=result.staged_count,
            ready_count=result.ready_count,
            blocked_count=result.blocked_count,
            already_staged_count=result.already_staged_count,
            limit_applied=result.limit_applied,
            product_snapshot_id=result.product_snapshot_id,
            product_snapshot_valid=result.product_snapshot_valid,
            force_mock_output=result.force_mock_output,
            real_gpt_called=result.real_gpt_called,
            message="پیام‌های کمپین آماده شد.",
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="Failed to prepare campaign messages."
        ) from exc


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

    items = [
        recipient_to_response(recipient, contact, staged)
        for recipient, contact, staged in rows
    ]

    return CampaignRecipientsListResponse(
        campaign_id=campaign_id,
        items=items,
        total_count=total_count,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{campaign_id}/recipients/{recipient_id}",
    response_model=CampaignRecipientDetailResponse,
)
def get_campaign_recipient_detail(
    campaign_id: int,
    recipient_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str],
        Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR, RoleType.VIEWER)),
    ] = None,
):
    """Full exact-text trace for one message-log row. No secrets."""
    return fetch_recipient_detail(db, campaign_id, recipient_id)


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

    all_import_contacts = resolve_contacts_for_import_batch(
        db, payload.import_batch_id
    )
    eligible_contacts, _skipped = filter_eligible(all_import_contacts)

    if not eligible_contacts:
        raise HTTPException(
            status_code=400,
            detail="No eligible contacts found for this import.",
        )

    skipped_contacts_count = len(all_import_contacts) - len(eligible_contacts)

    try:
        campaign, contacts_attached_count, senders, auto_prepare, sender_assignment = (
            _create_campaign_draft_from_eligible_contacts(
                db=db,
                current_user=current_user,
                title=payload.title,
                platform=payload.platform,
                template_text=payload.template_text,
                use_gpt=payload.use_gpt,
                include_products=payload.include_products,
                account_ids=payload.account_ids,
                eligible_contacts=eligible_contacts,
                skipped_contacts_count=skipped_contacts_count,
                audit_source="import_batch",
                audit_extra_details={"import_batch_id": payload.import_batch_id},
            )
        )
        return CampaignFromImportResponse(
            status="draft_created",
            campaign_id=campaign.id,
            import_batch_id=payload.import_batch_id,
            contacts_attached_count=contacts_attached_count,
            skipped_contacts_count=skipped_contacts_count,
            message="Campaign draft created from import successfully",
            account_ids=[sender.account_id for sender in senders],
            sender_accounts=senders,
            auto_prepare=auto_prepare,
            sender_assignment=sender_assignment,
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


@router.post("/from-contacts", response_model=CampaignFromContactsResponse)
def create_campaign_from_contacts(
    payload: CampaignFromContactsRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))],
):
    contact_ids = payload.contact_ids

    found_contacts = db.query(Contact).filter(Contact.id.in_(contact_ids)).all()
    by_id = {c.id: c for c in found_contacts}

    missing_contact_ids = [cid for cid in contact_ids if cid not in by_id]
    if missing_contact_ids:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "contact_not_found",
                "message": "One or more contacts were not found.",
                "missing_contact_ids": missing_contact_ids,
            },
        )

    eligible_contacts: list[Contact] = []
    skipped_contacts: list[dict[str, object]] = []
    for cid in contact_ids:
        contact = by_id[cid]
        if is_contact_deleted(contact):
            skipped_contacts.append(
                {"contact_id": cid, "reason_code": "deleted"}
            )
            continue
        if contact.blacklisted:
            skipped_contacts.append(
                {"contact_id": cid, "reason_code": "blacklisted"}
            )
            continue
        if contact.consent_status == ConsentStatus.BLOCKED.value:
            skipped_contacts.append(
                {"contact_id": cid, "reason_code": "consent_blocked"}
            )
            continue
        eligible_contacts.append(contact)

    skipped_contacts_count = len(contact_ids) - len(eligible_contacts)
    if not eligible_contacts:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "no_eligible_contacts",
                "message": "No eligible contacts found for this request.",
            },
        )

    try:
        campaign, contacts_attached_count, senders, auto_prepare, sender_assignment = (
            _create_campaign_draft_from_eligible_contacts(
                db=db,
                current_user=current_user,
                title=payload.title,
                platform=payload.platform,
                template_text=payload.template_text,
                use_gpt=payload.use_gpt,
                include_products=payload.include_products,
                account_ids=payload.account_ids,
                eligible_contacts=eligible_contacts,
                skipped_contacts_count=skipped_contacts_count,
                audit_source="existing_contacts",
                audit_extra_details={"contact_ids": contact_ids},
            )
        )

        return CampaignFromContactsResponse(
            status="draft_created",
            campaign_id=campaign.id,
            contacts_attached_count=contacts_attached_count,
            skipped_contacts_count=skipped_contacts_count,
            message="Campaign draft created from existing contacts successfully",
            account_ids=[sender.account_id for sender in senders],
            sender_accounts=senders,
            skipped_contacts=skipped_contacts,  # type: ignore[arg-type]
            auto_prepare=auto_prepare,
            sender_assignment=sender_assignment,
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Failed to create campaign from existing contacts.",
        ) from exc


@router.post("/audience-preview", response_model=AudiencePreviewResponse)
def preview_tag_audience(
    payload: AudiencePreviewRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))],
):
    tags = read_contact_tags(payload.selected_tags)
    if not tags:
        raise HTTPException(status_code=400, detail="At least one tag is required.")
    eligible, skipped = resolve_tag_audience(db, tags, match=payload.tag_match)
    return AudiencePreviewResponse(
        selected_tags=tags,
        tag_match=payload.tag_match,
        eligible_count=len(eligible),
        skipped_count=len(skipped),
        contact_ids=[int(contact.id) for contact in eligible],
    )


@router.post("/from-tags", response_model=CampaignFromTagsResponse)
def create_campaign_from_tags(
    payload: CampaignFromTagsRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))],
):
    eligible, skipped = resolve_tag_audience(
        db, payload.selected_tags, match=payload.tag_match
    )
    if not eligible:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "no_eligible_contacts",
                "message": "No eligible contacts found for the selected tags.",
            },
        )
    try:
        campaign, contacts_attached_count, senders, auto_prepare, sender_assignment = (
            _create_campaign_draft_from_eligible_contacts(
                db=db,
                current_user=current_user,
                title=payload.title,
                platform=payload.platform,
                template_text=payload.template_text,
                use_gpt=payload.use_gpt,
                include_products=payload.include_products,
                account_ids=payload.account_ids,
                eligible_contacts=eligible,
                skipped_contacts_count=len(skipped),
                audit_source="tags",
                audit_extra_details={
                    "selected_tags": payload.selected_tags,
                    "tag_match": payload.tag_match,
                },
            )
        )
        return CampaignFromTagsResponse(
            status="draft_created",
            campaign_id=campaign.id,
            selected_tags=payload.selected_tags,
            tag_match=payload.tag_match,
            contacts_attached_count=contacts_attached_count,
            skipped_contacts_count=len(skipped),
            message="Campaign draft created from tags successfully",
            account_ids=[sender.account_id for sender in senders],
            sender_accounts=senders,
            auto_prepare=auto_prepare,
            sender_assignment=sender_assignment,
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Failed to create campaign from tags.",
        ) from exc
