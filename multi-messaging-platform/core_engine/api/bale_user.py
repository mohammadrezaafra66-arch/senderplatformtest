"""API endpoints برای مدیریت اکانت‌های شخصی بله (User Account با aiobale)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core_engine.database import get_db
from core_engine.models import Account, AccountStatus, BaleAccountPool, BaleSenderSchedule
from core_engine.services.bale_user_session import (
    BaleLoginError,
    start_bale_user_login,
    verify_bale_user_login,
)

router = APIRouter(prefix="/bale/user", tags=["bale-user-account"])
logger = logging.getLogger("core_engine.api.bale_user")


# ─── schemas ───────────────────────────────────────────────────────────────

class StartLoginRequest(BaseModel):
    account_id: int
    phone_number: str


class StartLoginResponse(BaseModel):
    status: str
    registration_token: str
    phone_number: str


class VerifyLoginRequest(BaseModel):
    account_id: int
    registration_token: str
    code: str


class VerifyLoginResponse(BaseModel):
    status: str
    user_id: int | None = None
    phone_number: str | None = None


class PoolEntryResponse(BaseModel):
    account_id: int
    account_name: str | None
    phone: str | None
    is_healthy: bool
    sent_today: int
    daily_cap_today: int
    account_status: str


class ScheduleResponse(BaseModel):
    id: int
    start_hour: int
    end_hour: int
    is_active: bool


class UpdateScheduleRequest(BaseModel):
    start_hour: int
    end_hour: int
    is_active: bool


class UpdateCapRequest(BaseModel):
    account_id: int
    daily_cap: int


# ─── endpoints ─────────────────────────────────────────────────────────────

@router.post("/login/start", response_model=StartLoginResponse)
async def start_login(req: StartLoginRequest):
    """مرحله اول لاگین — ارسال کد OTP به شماره موبایل."""
    try:
        result = await start_bale_user_login(
            account_id=req.account_id,
            phone_number=req.phone_number,
        )
    except BaleLoginError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("bale_login_start_error account_id=%s", req.account_id)
        raise HTTPException(status_code=500, detail=f"خطای داخلی: {exc}")
    return result


@router.post("/login/verify", response_model=VerifyLoginResponse)
async def verify_login(req: VerifyLoginRequest, db: Session = Depends(get_db)):
    """مرحله دوم لاگین — تأیید کد OTP و ذخیره session."""
    try:
        result = await verify_bale_user_login(
            db=db,
            registration_token=req.registration_token,
            code=req.code,
            account_id=req.account_id,
        )
    except BaleLoginError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("bale_login_verify_error account_id=%s", req.account_id)
        raise HTTPException(status_code=500, detail=f"خطای داخلی: {exc}")
    return result


@router.get("/pool", response_model=list[PoolEntryResponse])
def get_pool(db: Session = Depends(get_db)):
    """لیست تمام اکانت‌های pool بله."""
    rows = (
        db.query(BaleAccountPool, Account)
        .join(Account, Account.id == BaleAccountPool.account_id)
        .all()
    )
    result = []
    for pool_entry, account in rows:
        result.append(PoolEntryResponse(
            account_id=account.id,
            account_name=account.label,
            phone=account.phone_number,
            is_healthy=pool_entry.is_healthy,
            sent_today=pool_entry.sent_today,
            daily_cap_today=pool_entry.daily_cap_today,
            account_status=account.status.value,
        ))
    return result


@router.post("/pool/{account_id}/reset")
def reset_account_health(account_id: int, db: Session = Depends(get_db)):
    """ریست کردن وضعیت سلامت یک اکانت."""
    pool_entry = (
        db.query(BaleAccountPool)
        .filter(BaleAccountPool.account_id == account_id)
        .first()
    )
    if not pool_entry:
        raise HTTPException(status_code=404, detail="اکانت در pool بله نیست.")
    pool_entry.is_healthy = True
    pool_entry.last_error_at = None
    pool_entry.last_error_message = None
    account = db.query(Account).filter(Account.id == account_id).first()
    if account and account.status == AccountStatus.BANNED:
        account.status = AccountStatus.ACTIVE
    db.commit()
    return {"status": "reset", "account_id": account_id}


@router.post("/pool/{account_id}/remove")
def remove_from_pool(account_id: int, db: Session = Depends(get_db)):
    """حذف اکانت از pool بله."""
    pool_entry = (
        db.query(BaleAccountPool)
        .filter(BaleAccountPool.account_id == account_id)
        .first()
    )
    if not pool_entry:
        raise HTTPException(status_code=404, detail="اکانت در pool بله نیست.")
    db.delete(pool_entry)
    db.commit()
    return {"status": "removed", "account_id": account_id}


@router.post("/pool/cap")
def update_daily_cap(req: UpdateCapRequest, db: Session = Depends(get_db)):
    """تغییر سقف روزانه ارسال یک اکانت."""
    pool_entry = (
        db.query(BaleAccountPool)
        .filter(BaleAccountPool.account_id == req.account_id)
        .first()
    )
    if not pool_entry:
        raise HTTPException(status_code=404, detail="اکانت در pool بله نیست.")
    pool_entry.daily_cap_today = req.daily_cap
    db.commit()
    return {"status": "updated", "account_id": req.account_id, "daily_cap": req.daily_cap}


@router.get("/schedules", response_model=list[ScheduleResponse])
def get_schedules(db: Session = Depends(get_db)):
    """لیست بازه‌های زمانی ارسال بله."""
    schedules = db.query(BaleSenderSchedule).order_by(BaleSenderSchedule.id).all()
    return [
        ScheduleResponse(
            id=s.id,
            start_hour=s.start_hour,
            end_hour=s.end_hour,
            is_active=s.is_active,
        )
        for s in schedules
    ]


@router.post("/schedules")
def create_schedule(req: UpdateScheduleRequest, db: Session = Depends(get_db)):
    """ساخت بازه زمانی جدید."""
    schedule = BaleSenderSchedule(
        start_hour=req.start_hour,
        end_hour=req.end_hour,
        is_active=req.is_active,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return {"status": "created", "id": schedule.id}


@router.put("/schedules/{schedule_id}")
def update_schedule(
    schedule_id: int, req: UpdateScheduleRequest, db: Session = Depends(get_db)
):
    """آپدیت بازه زمانی."""
    schedule = db.query(BaleSenderSchedule).filter(BaleSenderSchedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="بازه زمانی پیدا نشد.")
    schedule.start_hour = req.start_hour
    schedule.end_hour = req.end_hour
    schedule.is_active = req.is_active
    db.commit()
    return {"status": "updated", "id": schedule_id}


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    """حذف بازه زمانی."""
    schedule = db.query(BaleSenderSchedule).filter(BaleSenderSchedule.id == schedule_id).first()
    if not schedule:
        raise HTTPException(status_code=404, detail="بازه زمانی پیدا نشد.")
    db.delete(schedule)
    db.commit()
    return {"status": "deleted", "id": schedule_id}
