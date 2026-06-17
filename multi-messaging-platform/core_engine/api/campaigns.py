"""API مدیریت کمپین‌های ارسال."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core_engine.api.schemas import (
    CampaignDetailResponse,
    CampaignFromImportRequest,
    CampaignFromImportResponse,
    CampaignListItemResponse,
    CampaignsListResponse,
    CampaignStatsData,
)
from core_engine.database import get_db
from core_engine.models import (
    Campaign,
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
from core_engine.services.dashboard import get_campaign_stats
from core_engine.services.rbac import requires_role

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


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
    query = db.query(Campaign)

    if status:
        query = query.filter(Campaign.status == status)
    if platform:
        query = query.filter(Campaign.platform == platform)

    total_count = query.count()

    campaigns = query.order_by(Campaign.created_at.desc()).limit(limit).offset(offset).all()

    items = []
    for campaign in campaigns:
        recipient_count = db.query(CampaignRecipient).filter(CampaignRecipient.campaign_id == campaign.id).count()
        items.append(CampaignListItemResponse(
            id=campaign.id,
            name=campaign.name,
            title=campaign.title,
            platform=campaign.platform,
            status=campaign.status,
            created_at=campaign.created_at,
            total_recipients=recipient_count,
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
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    stats_dict = get_campaign_stats(db, campaign_id)
    stats = CampaignStatsData(**stats_dict)

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

        return CampaignFromImportResponse(
            status="draft_created",
            campaign_id=campaign.id,
            import_batch_id=payload.import_batch_id,
            contacts_attached_count=contacts_attached_count,
            skipped_contacts_count=skipped_contacts_count,
            message="Campaign draft created from import successfully",
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
