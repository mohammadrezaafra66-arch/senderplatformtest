"""Endpoint های مدیریت MTProto تلگرام."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core_engine.database import get_db
from core_engine.services.telegram_session_setup import start_phone_login, verify_phone_code
from core_engine.models import TelegramAccountPool, TelegramSenderSchedule, TelegramMTProtoLead

router = APIRouter(prefix="/telegram-mtproto", tags=["telegram-mtproto"])


class StartLoginRequest(BaseModel):
    account_id: int
    phone_number: str


class VerifyCodeRequest(BaseModel):
    account_id: int
    phone_number: str
    code: str
    two_step_password: str | None = None


@router.post("/session/start")
async def session_start(body: StartLoginRequest):
    return await start_phone_login(body.account_id, body.phone_number)


@router.post("/session/verify")
async def session_verify(body: VerifyCodeRequest, db: Session = Depends(get_db)):
    return await verify_phone_code(
        db, body.account_id, body.phone_number, body.code, body.two_step_password
    )


@router.get("/accounts/pool")
def list_account_pool(db: Session = Depends(get_db)):
    rows = db.query(TelegramAccountPool).all()
    return [
        {
            "account_id": r.account_id,
            "is_warmed_up": r.is_warmed_up,
            "daily_cap_today": r.daily_cap_today,
            "sent_today": r.sent_today,
            "is_healthy": r.is_healthy,
            "last_error_message": r.last_error_message,
        }
        for r in rows
    ]


@router.get("/schedule")
def get_schedule(db: Session = Depends(get_db)):
    row = db.query(TelegramSenderSchedule).filter(TelegramSenderSchedule.is_active == True).first()
    if not row:
        return {"start_hour": 9, "end_hour": 21}
    return {"start_hour": row.start_hour, "end_hour": row.end_hour}


class ScheduleUpdate(BaseModel):
    start_hour: int
    end_hour: int


@router.put("/schedule")
def update_schedule(body: ScheduleUpdate, db: Session = Depends(get_db)):
    row = db.query(TelegramSenderSchedule).filter(TelegramSenderSchedule.is_active == True).first()
    if row:
        row.start_hour = body.start_hour
        row.end_hour = body.end_hour
    else:
        row = TelegramSenderSchedule(start_hour=body.start_hour, end_hour=body.end_hour)
        db.add(row)
    db.commit()
    return {"status": "updated"}


@router.get("/leads")
def list_leads(db: Session = Depends(get_db), limit: int = 100):
    rows = (
        db.query(TelegramMTProtoLead)
        .order_by(TelegramMTProtoLead.last_activity_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "phone_number": r.phone_number,
            "username": r.username,
            "source": r.source,
            "first_seen_at": r.first_seen_at.isoformat(),
        }
        for r in rows
    ]
