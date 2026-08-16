"""API ماژول روبیکا v2 — فقط بخش‌هایی که واقعاً جدیدند.

عمداً endpoint جدا برای کمپین/ایمپورت/دستیار قیمت نساخته‌ایم چون این‌ها از قبل
به‌صورت کاملاً platform-agnostic موجودند و روبیکا (هم بات‌اپی‌آی هم user_account)
از همان مسیر رد می‌شود:
  - آپلود/پیش‌نمایش/ثبت اکسل  → POST /imports/contacts/preview ، /imports/contacts/commit
  - ساخت کمپین از ایمپورت     → POST /campaigns/from-import  (با platform=rubika)
  - شروع/توقف کمپین           → POST /campaigns/{id}/start ، /stop
  - جزئیات و آمار کمپین        → GET /campaigns/{id}
  - قیمت لحظه‌ای (دستیار افراکالا) → GET /debug/pricing-cache ، POST /debug/pricing-cache/refresh
  - ثبت/تأیید session اکانت شخصی → POST /accounts/{id}/rubika/session/register و .../verify (فاز ۲)

اینجا فقط چیزهایی هست که جدول دیتابیسش در فاز ۱ تازه ساخته شده:
استخر چند اکانتی، لاگ ارسال مخصوص روبیکا، مدیریت گروه‌های پایش، زمان‌بندی روز/شب.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core_engine.api.schemas import (
    RubikaAccountsListResponse,
    RubikaGroupCreateRequest,
    RubikaGroupMessageItem,
    RubikaGroupMessagesResponse,
    RubikaGroupResponse,
    RubikaGroupsListResponse,
    RubikaGroupUpdateRequest,
    RubikaPoolAccountItem,
    RubikaPoolRestoreResponse,
    RubikaPoolUpsertRequest,
    RubikaPoolUpsertResponse,
    RubikaScheduleItem,
    RubikaScheduleListResponse,
    RubikaScheduleUpdateRequest,
    RubikaSendLogItem,
    RubikaSendLogResponse,
)
from core_engine.database import get_db
from core_engine.models import (
    Account,
    Campaign,
    Contact,
    Message,
    MessageAttempt,
    PlatformType,
    RoleType,
    RubikaAccountPool,
    RubikaAllowedGroup,
    RubikaContentSchedule,
    RubikaGroupMessage,
    RubikaSenderSchedule,
)
from core_engine.services.audit_service import record_audit
from core_engine.services.rbac import requires_role
from workers.rubika_account_pool import RubikaAccountPoolManager

router = APIRouter(prefix="/rubika", tags=["rubika"])

SENDING_PHASES = {"day", "night"}
NON_SENDING_PHASES = {"listener", "status"}


# ─────────────────────────────────────────────────────────────────
# استخر اکانت‌ها
# ─────────────────────────────────────────────────────────────────


@router.get("/accounts", response_model=RubikaAccountsListResponse)
def list_rubika_pool_accounts(
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    """همه اکانت‌های روبیکا، با ردیف pool هر کدام (اگر در استخری عضو باشند)."""
    rows = (
        db.query(Account, RubikaAccountPool)
        .outerjoin(RubikaAccountPool, RubikaAccountPool.account_id == Account.id)
        .filter(Account.platform == PlatformType.RUBIKA)
        .order_by(Account.id.asc())
        .all()
    )

    items = [
        RubikaPoolAccountItem(
            account_id=account.id,
            label=account.label,
            phone_number=account.phone_number,
            account_status=account.status.value,
            phase=pool_row.phase if pool_row else "unassigned",
            priority=pool_row.priority if pool_row else 0,
            last_error_at=pool_row.last_error_at if pool_row else None,
            last_error_message=pool_row.last_error_message if pool_row else None,
            last_used_at=account.last_used_at,
        )
        for account, pool_row in rows
    ]
    return RubikaAccountsListResponse(items=items, total_count=len(items))


@router.post("/accounts/{account_id}/pool", response_model=RubikaPoolUpsertResponse)
def upsert_rubika_pool_membership(
    account_id: int,
    payload: RubikaPoolUpsertRequest,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """افزودن/به‌روزرسانی عضویت یک اکانت در یک فاز.

    قانون امنیتی سند (بخش هفت): اکانت ارسال (day/night) و اکانت پایش/استاتوس
    (listener/status) باید کاملاً مجزا باشند — این‌جا اعمال می‌شود.
    """
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    if account.platform != PlatformType.RUBIKA:
        raise HTTPException(status_code=400, detail="این اکانت پلتفرم روبیکا نیست.")

    existing_phases = {
        row.phase
        for row in db.query(RubikaAccountPool)
        .filter(RubikaAccountPool.account_id == account_id)
        .all()
    }

    if payload.phase in SENDING_PHASES and existing_phases & NON_SENDING_PHASES:
        raise HTTPException(
            status_code=400,
            detail=(
                "این اکانت در حال حاضر برای پایش/استاتوس استفاده می‌شود — طبق قانون "
                "امنیتی سند نمی‌تواند هم‌زمان اکانت ارسال هم باشد. اکانت مجزا انتخاب کنید."
            ),
        )
    if payload.phase in NON_SENDING_PHASES and existing_phases & SENDING_PHASES:
        raise HTTPException(
            status_code=400,
            detail=(
                "این اکانت در حال حاضر برای ارسال (روز/شب) استفاده می‌شود — طبق قانون "
                "امنیتی سند نمی‌تواند هم‌زمان اکانت پایش/استاتوس هم باشد."
            ),
        )

    row = (
        db.query(RubikaAccountPool)
        .filter(
            RubikaAccountPool.account_id == account_id,
            RubikaAccountPool.phase == payload.phase,
        )
        .first()
    )
    if row is None:
        row = RubikaAccountPool(
            account_id=account_id, phase=payload.phase, priority=payload.priority
        )
        db.add(row)
    else:
        row.priority = payload.priority

    record_audit(
        db, current_user["username"], "rubika_pool_upsert", "account", str(account_id),
        {"phase": payload.phase, "priority": payload.priority},
    )
    db.commit()

    return RubikaPoolUpsertResponse(
        success=True, account_id=account_id, phase=payload.phase,
        priority=payload.priority, message="عضویت در استخر ثبت شد.",
    )


@router.delete("/accounts/{account_id}/pool/{phase}")
def remove_rubika_pool_membership(
    account_id: int,
    phase: str,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    deleted = (
        db.query(RubikaAccountPool)
        .filter(
            RubikaAccountPool.account_id == account_id,
            RubikaAccountPool.phase == phase,
        )
        .delete()
    )
    record_audit(
        db, current_user["username"], "rubika_pool_remove", "account", str(account_id),
        {"phase": phase},
    )
    db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="این اکانت در این فاز عضو نبود.")
    return {"success": True, "account_id": account_id, "phase": phase}


@router.post("/accounts/{account_id}/pool/restore", response_model=RubikaPoolRestoreResponse)
def restore_rubika_account(
    account_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    """RESTING → ACTIVE بعد از بازبینی دستی. اکانت BANNED را تغییر نمی‌دهد."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")

    pool = RubikaAccountPoolManager(db)
    pool.mark_account_restored(account_id=account_id)
    db.refresh(account)

    record_audit(
        db, current_user["username"], "rubika_pool_restore", "account", str(account_id),
        {"new_status": account.status.value},
    )
    db.commit()

    return RubikaPoolRestoreResponse(
        success=True, account_id=account_id, account_status=account.status.value,
        message="وضعیت اکانت بررسی/به‌روزرسانی شد.",
    )


# ─────────────────────────────────────────────────────────────────
# لاگ ارسال (همه کمپین‌های روبیکا، نه فقط یک کمپین)
# ─────────────────────────────────────────────────────────────────


@router.get("/send-log", response_model=RubikaSendLogResponse)
def rubika_send_log(
    limit: int = 50,
    offset: int = 0,
    account_id: int | None = None,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str],
        Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR, RoleType.VIEWER)),
    ] = None,
):
    query = (
        db.query(Message, Campaign, Account, Contact)
        .join(Campaign, Campaign.id == Message.campaign_id)
        .join(Account, Account.id == Message.account_id)
        .join(Contact, Contact.id == Message.contact_id)
        .filter(Account.platform == PlatformType.RUBIKA)
    )
    if account_id is not None:
        query = query.filter(Message.account_id == account_id)

    total_count = query.count()
    rows = query.order_by(Message.created_at.desc()).limit(limit).offset(offset).all()

    items = []
    for message, campaign, account, contact in rows:
        latest_attempt = (
            db.query(MessageAttempt)
            .filter(MessageAttempt.message_id == message.id)
            .order_by(MessageAttempt.attempt_no.desc())
            .first()
        )
        items.append(
            RubikaSendLogItem(
                message_id=message.id,
                campaign_id=campaign.id,
                campaign_title=campaign.title,
                account_id=account.id,
                account_label=account.label,
                contact_id=contact.id,
                contact_phone=contact.phone_e164 or contact.phone,
                rendered_text=message.rendered_text,
                status=latest_attempt.status.value if latest_attempt else None,
                platform_message_id=latest_attempt.platform_message_id if latest_attempt else None,
                error_code=latest_attempt.error_code if latest_attempt else None,
                error_message=latest_attempt.error_message if latest_attempt else None,
                created_at=message.created_at,
            )
        )

    return RubikaSendLogResponse(items=items, total_count=total_count, limit=limit, offset=offset)


# ─────────────────────────────────────────────────────────────────
# گروه‌های مجاز برای پایش
# ─────────────────────────────────────────────────────────────────


def _group_to_response(group: RubikaAllowedGroup) -> RubikaGroupResponse:
    return RubikaGroupResponse(
        id=group.id,
        group_guid=group.group_guid,
        group_name=group.group_name,
        listener_account_id=group.listener_account_id,
        keywords=group.keywords or [],
        keyword_response=group.keyword_response,
        red_keywords=group.red_keywords or [],
        conversation_mode_enabled=group.conversation_mode_enabled,
        is_active=group.is_active,
        created_at=group.created_at,
    )


@router.get("/groups", response_model=RubikaGroupsListResponse)
def list_rubika_groups(
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    groups = db.query(RubikaAllowedGroup).order_by(RubikaAllowedGroup.id.asc()).all()
    items = [_group_to_response(g) for g in groups]
    return RubikaGroupsListResponse(items=items, total_count=len(items))


@router.post("/groups", response_model=RubikaGroupResponse, status_code=201)
def create_rubika_group(
    payload: RubikaGroupCreateRequest,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    existing = (
        db.query(RubikaAllowedGroup)
        .filter(RubikaAllowedGroup.group_guid == payload.group_guid)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="این group_guid قبلاً ثبت شده است.")

    group = RubikaAllowedGroup(
        group_guid=payload.group_guid,
        group_name=payload.group_name,
        listener_account_id=payload.listener_account_id,
        keywords=payload.keywords,
        keyword_response=payload.keyword_response,
        red_keywords=payload.red_keywords,
        conversation_mode_enabled=payload.conversation_mode_enabled,
    )
    db.add(group)
    db.flush()

    record_audit(
        db, current_user["username"], "rubika_group_create", "rubika_group", str(group.id),
        {"group_guid": payload.group_guid},
    )
    db.commit()
    db.refresh(group)
    return _group_to_response(group)


@router.put("/groups/{group_id}", response_model=RubikaGroupResponse)
def update_rubika_group(
    group_id: int,
    payload: RubikaGroupUpdateRequest,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    group = db.query(RubikaAllowedGroup).filter(RubikaAllowedGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="گروه پیدا نشد.")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(group, field, value)

    record_audit(
        db, current_user["username"], "rubika_group_update", "rubika_group", str(group_id),
        update_data,
    )
    db.commit()
    db.refresh(group)
    return _group_to_response(group)


@router.delete("/groups/{group_id}")
def delete_rubika_group(
    group_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    deleted = db.query(RubikaAllowedGroup).filter(RubikaAllowedGroup.id == group_id).delete()
    record_audit(
        db, current_user["username"], "rubika_group_delete", "rubika_group", str(group_id), {}
    )
    db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="گروه پیدا نشد.")
    return {"success": True, "group_id": group_id}


@router.get("/groups/{group_id}/messages", response_model=RubikaGroupMessagesResponse)
def list_rubika_group_messages(
    group_id: int,
    limit: int = 50,
    offset: int = 0,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str],
        Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR, RoleType.VIEWER)),
    ] = None,
):
    group = db.query(RubikaAllowedGroup).filter(RubikaAllowedGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="گروه پیدا نشد.")

    query = db.query(RubikaGroupMessage).filter(
        RubikaGroupMessage.group_guid == group.group_guid
    )
    total_count = query.count()
    rows = (
        query.order_by(RubikaGroupMessage.received_at.desc()).limit(limit).offset(offset).all()
    )

    items = [
        RubikaGroupMessageItem(
            id=m.id,
            sender_name=m.sender_name,
            sender_phone=m.sender_phone,
            message_type=m.message_type,
            message_text=m.message_text,
            transcription=m.transcription,
            image_extracted_text=m.image_extracted_text,
            is_reply_to_our_message=m.is_reply_to_our_message,
            has_red_keyword=m.has_red_keyword,
            received_at=m.received_at,
        )
        for m in rows
    ]

    return RubikaGroupMessagesResponse(
        group_id=group_id, items=items, total_count=total_count, limit=limit, offset=offset
    )


# ─────────────────────────────────────────────────────────────────
# زمان‌بندی روز/شب — مقادیر پیش‌فرض در migration فاز ۱ seed شده‌اند
# ─────────────────────────────────────────────────────────────────


@router.get("/schedule", response_model=RubikaScheduleListResponse)
def get_rubika_schedule(
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    rows = db.query(RubikaSenderSchedule).order_by(RubikaSenderSchedule.id.asc()).all()
    items = [
        RubikaScheduleItem(
            phase=r.phase, start_hour=r.start_hour, end_hour=r.end_hour,
            max_per_hour=r.max_per_hour, is_active=r.is_active,
        )
        for r in rows
    ]
    return RubikaScheduleListResponse(items=items)


@router.put("/schedule/{phase}", response_model=RubikaScheduleItem)
def update_rubika_schedule(
    phase: str,
    payload: RubikaScheduleUpdateRequest,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    row = db.query(RubikaSenderSchedule).filter(RubikaSenderSchedule.phase == phase).first()
    if row is None:
        row = RubikaSenderSchedule(phase=phase)
        db.add(row)

    row.start_hour = payload.start_hour
    row.end_hour = payload.end_hour
    row.max_per_hour = payload.max_per_hour
    row.is_active = payload.is_active

    record_audit(
        db, current_user["username"], "rubika_schedule_update", "rubika_schedule", phase,
        payload.model_dump(),
    )
    db.commit()
    db.refresh(row)

    return RubikaScheduleItem(
        phase=row.phase, start_hour=row.start_hour, end_hour=row.end_hour,
        max_per_hour=row.max_per_hour, is_active=row.is_active,
    )


# ─────────────────────────────────────────────────────────────────
# زمان‌بندی محتوای استاتوس (Rubino) — نیازمندی ۲۴ سند
# ─────────────────────────────────────────────────────────────────


class RubikaContentScheduleCreateRequest(BaseModel):
    caption: str | None = None
    media_path: str | None = None
    content_type: str = "Picture"
    scheduled_at: str  # ISO 8601 string


def _content_schedule_to_dict(row: RubikaContentSchedule) -> dict:
    return {
        "id": row.id,
        "caption": row.caption,
        "media_path": row.media_path,
        "content_type": row.content_type,
        "scheduled_at": row.scheduled_at,
        "published": row.published,
        "published_at": row.published_at,
        "error_message": row.error_message,
        "created_at": row.created_at,
    }


@router.get("/content-schedule")
def list_rubika_content_schedule(
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    rows = (
        db.query(RubikaContentSchedule)
        .order_by(RubikaContentSchedule.scheduled_at.asc())
        .all()
    )
    items = [_content_schedule_to_dict(r) for r in rows]
    return {"items": items, "total_count": len(items)}


@router.post("/content-schedule", status_code=201)
def create_rubika_content_schedule(
    payload: RubikaContentScheduleCreateRequest,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    try:
        scheduled_at = datetime.fromisoformat(payload.scheduled_at)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="scheduled_at باید یک رشته ISO 8601 معتبر باشد."
        )

    row = RubikaContentSchedule(
        caption=payload.caption,
        media_path=payload.media_path,
        content_type=payload.content_type,
        scheduled_at=scheduled_at,
    )
    db.add(row)
    db.flush()

    record_audit(
        db, current_user["username"], "rubika_content_schedule_create",
        "rubika_content_schedule", str(row.id),
        {"content_type": payload.content_type, "scheduled_at": payload.scheduled_at},
    )
    db.commit()
    db.refresh(row)
    return _content_schedule_to_dict(row)


@router.delete("/content-schedule/{schedule_id}")
def delete_rubika_content_schedule(
    schedule_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    deleted = (
        db.query(RubikaContentSchedule)
        .filter(RubikaContentSchedule.id == schedule_id)
        .delete()
    )
    record_audit(
        db, current_user["username"], "rubika_content_schedule_delete",
        "rubika_content_schedule", str(schedule_id), {},
    )
    db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="آیتم زمان‌بندی پیدا نشد.")
    return {"success": True, "schedule_id": schedule_id}


# ─────────────────────────────────────────────────────────────────
# Phase 4 — health / protection / incidents / restore
# ─────────────────────────────────────────────────────────────────


@router.get("/health")
async def rubika_system_health(
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    from core_engine.services.redis_client import ensure_redis_client
    from core_engine.services.rubika_circuit import get_circuit_snapshot
    from core_engine.services.rubika_incidents import list_open_incidents
    from workers.config import get_worker_settings

    settings = get_worker_settings()
    redis = await ensure_redis_client()
    snap = await get_circuit_snapshot(
        redis,
        probe_budget=int(settings.RUBIKA_CIRCUIT_PROBE_BUDGET),
        window_seconds=int(settings.RUBIKA_CIRCUIT_WINDOW_SECONDS),
    )
    incidents = await list_open_incidents(redis)
    return {
        "state": snap.state,
        "code": "RUBIKA_CIRCUIT_OPEN" if snap.state == "open" else "OK",
        "message": "وضعیت حفاظت سراسری روبیکا",
        "circuit": {
            "state": snap.state,
            "opened_at": snap.opened_at,
            "half_open_at": snap.half_open_at,
            "open_until": snap.open_until,
            "probe_budget": snap.probe_budget,
            "probe_remaining": snap.probe_remaining,
            "reason": snap.reason,
        },
        "open_incidents": len(incidents),
    }


@router.get("/protection/status")
async def rubika_protection_status(
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    return await rubika_system_health(current_user=current_user)


@router.get("/accounts/{account_id}/health")
async def rubika_account_health(
    account_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    from core_engine.services.redis_client import ensure_redis_client
    from core_engine.services.rubika_health import build_health_snapshot

    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise HTTPException(status_code=404, detail="اکانت پیدا نشد.")
    if account.platform != PlatformType.RUBIKA:
        raise HTTPException(status_code=400, detail="اکانت روبیکا نیست.")
    redis = await ensure_redis_client()
    snap = await build_health_snapshot(redis, account)
    return {
        "state": snap.health_state,
        "code": "ACCOUNT_QUARANTINED" if snap.quarantined else "OK",
        "message": "وضعیت سلامت اکانت روبیکا",
        "health": snap.to_dict(),
    }


@router.get("/incidents")
async def rubika_list_incidents(
    status: str = "OPEN",
    scope: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    account_id: int | None = None,
    sort: str = "newest",
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    from core_engine.services.redis_client import ensure_redis_client
    from core_engine.services.rubika_incidents import list_incidents

    redis = await ensure_redis_client()
    items = await list_incidents(
        redis,
        status=status,
        scope=scope,
        severity=severity,
        category=category,
        account_id=account_id,
        sort=sort,
    )
    return {"items": [i.to_dict() for i in items], "count": len(items)}


@router.get("/incidents/{incident_id}")
async def rubika_get_incident(
    incident_id: str,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    from core_engine.services.redis_client import ensure_redis_client
    from core_engine.services.rubika_circuit import get_circuit_snapshot
    from core_engine.services.rubika_incidents import get_incident_by_id
    from workers.config import get_worker_settings

    redis = await ensure_redis_client()
    incident = await get_incident_by_id(redis, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="حادثه پیدا نشد.")
    settings = get_worker_settings()
    circuit = await get_circuit_snapshot(
        redis,
        probe_budget=int(settings.RUBIKA_CIRCUIT_PROBE_BUDGET),
        window_seconds=int(settings.RUBIKA_CIRCUIT_WINDOW_SECONDS),
    )
    return {
        "incident": incident.to_dict(),
        "related_circuit_state": circuit.state,
        "resolution_status": incident.status,
    }


@router.post("/incidents/{incident_id}/acknowledge")
async def rubika_acknowledge_incident(
    incident_id: str,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    from core_engine.services.redis_client import ensure_redis_client
    from core_engine.services.rubika_incidents import acknowledge_incident

    redis = await ensure_redis_client()
    incident = await acknowledge_incident(redis, incident_id=incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="حادثه پیدا نشد.")
    record_audit(
        db,
        current_user["username"],
        "rubika_incident_acknowledge",
        "incident",
        incident_id,
        {"result": "acknowledged", "scope": incident.scope, "account_id": incident.account_id},
    )
    db.commit()
    return {"state": "acknowledged", "incident": incident.to_dict()}


@router.get("/protection/overview")
async def rubika_protection_overview(
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    """Aggregate Protection Center payload — one call for system/accounts/incidents/alerts."""
    from core_engine.services.redis_client import ensure_redis_client
    from core_engine.services.rubika_operations import build_protection_overview

    redis = await ensure_redis_client()
    return await build_protection_overview(db, redis)


@router.get("/protection/events")
async def rubika_protection_events(
    limit: int = 50,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    from core_engine.services.redis_client import ensure_redis_client
    from core_engine.services.rubika_operations import build_protection_overview

    redis = await ensure_redis_client()
    overview = await build_protection_overview(
        db, redis, include_alerts_sync=False
    )
    events = overview.get("events") or []
    return {"items": events[: max(1, min(limit, 200))], "count": len(events)}


@router.get("/alerts")
async def rubika_list_alerts(
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    from core_engine.services.redis_client import ensure_redis_client
    from core_engine.services.rubika_alerts import list_open_alerts

    redis = await ensure_redis_client()
    items = await list_open_alerts(redis)
    return {"items": [i.to_dict() for i in items], "count": len(items)}


@router.post("/alerts/{dedupe_key}/acknowledge")
async def rubika_acknowledge_alert(
    dedupe_key: str,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    from core_engine.services.redis_client import ensure_redis_client
    from core_engine.services.rubika_alerts import acknowledge_alert

    redis = await ensure_redis_client()
    alert = await acknowledge_alert(redis, dedupe_key=dedupe_key)
    if alert is None:
        raise HTTPException(status_code=404, detail="هشدار پیدا نشد.")
    record_audit(
        db,
        current_user["username"],
        "rubika_alert_acknowledge",
        "alert",
        dedupe_key,
        {"result": "acknowledged", "type": alert.type, "account_id": alert.account_id},
    )
    db.commit()
    return {"state": "acknowledged", "alert": alert.to_dict()}


@router.get("/accounts/{account_id}/protection")
async def rubika_account_protection_detail(
    account_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str], Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR))
    ] = None,
):
    from core_engine.services.redis_client import ensure_redis_client
    from core_engine.services.rubika_operations import build_account_protection_detail

    redis = await ensure_redis_client()
    detail = await build_account_protection_detail(db, redis, account_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="اکانت پیدا نشد.")
    return detail


@router.post("/accounts/{account_id}/restore")
async def rubika_restore_account(
    account_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[dict[str, str], Depends(requires_role(RoleType.ADMIN))] = None,
):
    from core_engine.services.redis_client import ensure_redis_client
    from core_engine.services.rubika_alerts import (
        RubikaAlertSeverity,
        RubikaAlertType,
        upsert_alert,
    )
    from core_engine.services.rubika_health import restore_rubika_account

    redis = await ensure_redis_client()
    result = await restore_rubika_account(
        db,
        redis,
        account_id=account_id,
        username=current_user["username"],
        require_session_ready=True,
    )
    if not result.ok:
        raise HTTPException(
            status_code=400,
            detail={
                "state": "denied",
                "code": result.code,
                "message": result.message,
                "retryable": False,
            },
        )
    await upsert_alert(
        redis,
        alert_type=RubikaAlertType.ACCOUNT_RECOVERED.value,
        severity=RubikaAlertSeverity.INFO.value,
        title=f"اکانت {account_id} بازیابی شد",
        message="اپراتور اکانت را از قرنطینه بازگرداند.",
        account_id=account_id,
        category="recovery",
        metadata={"health_state": result.health_state},
        ttl_seconds=3600,
    )
    return {
        "state": "restored",
        "code": result.code,
        "message": result.message,
        "account_id": result.account_id,
        "health_state": result.health_state,
    }
