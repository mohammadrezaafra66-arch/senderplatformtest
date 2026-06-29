from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from core_engine.database import get_db
from core_engine.models import Account, AccountSendSettings

router = APIRouter(prefix="/accounts", tags=["send-settings"])

ABSOLUTE_FLOOR = 10  # ثانیه — کمتر از این ممنوع است


class SendSettingsUpdate(BaseModel):
    min_delay_seconds: int
    max_delay_seconds: int

    @field_validator("min_delay_seconds")
    @classmethod
    def min_must_be_above_floor(cls, v: int) -> int:
        if v < ABSOLUTE_FLOOR:
            raise ValueError(
                f"min_delay_seconds cannot be less than {ABSOLUTE_FLOOR} seconds"
            )
        return v

    @field_validator("max_delay_seconds")
    @classmethod
    def max_must_be_above_min(cls, v: int, info) -> int:
        min_v = info.data.get("min_delay_seconds")
        if min_v is not None and v < min_v:
            raise ValueError("max_delay_seconds must be >= min_delay_seconds")
        return v


class SendSettingsResponse(BaseModel):
    account_id: int
    min_delay_seconds: int
    max_delay_seconds: int
    floor_delay_seconds: int
    risk_level: str
    updated_at: datetime

    class Config:
        from_attributes = True


def _calc_risk(min_s: int) -> str:
    if min_s >= 45:
        return "safe"
    elif min_s >= 20:
        return "medium"
    elif min_s >= 10:
        return "high"
    return "blocked"


@router.get("/{account_id}/send-settings", response_model=SendSettingsResponse)
def get_send_settings(account_id: int, db: Session = Depends(get_db)):
    settings = (
        db.query(AccountSendSettings).filter_by(account_id=account_id).first()
    )
    if not settings:
        # برگرداندن پیشفرض اگر تنظیم نشده
        return SendSettingsResponse(
            account_id=account_id,
            min_delay_seconds=45,
            max_delay_seconds=90,
            floor_delay_seconds=10,
            risk_level="safe",
            updated_at=datetime.utcnow(),
        )
    return SendSettingsResponse(
        account_id=settings.account_id,
        min_delay_seconds=settings.min_delay_seconds,
        max_delay_seconds=settings.max_delay_seconds,
        floor_delay_seconds=settings.floor_delay_seconds,
        risk_level=_calc_risk(settings.min_delay_seconds),
        updated_at=settings.updated_at,
    )


@router.put("/{account_id}/send-settings", response_model=SendSettingsResponse)
def update_send_settings(
    account_id: int, body: SendSettingsUpdate, db: Session = Depends(get_db)
):
    account = db.query(Account).filter_by(id=account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    settings = (
        db.query(AccountSendSettings).filter_by(account_id=account_id).first()
    )
    if not settings:
        settings = AccountSendSettings(
            account_id=account_id,
            floor_delay_seconds=ABSOLUTE_FLOOR,
        )
        db.add(settings)

    settings.min_delay_seconds = max(body.min_delay_seconds, ABSOLUTE_FLOOR)
    settings.max_delay_seconds = body.max_delay_seconds
    settings.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(settings)

    return SendSettingsResponse(
        account_id=settings.account_id,
        min_delay_seconds=settings.min_delay_seconds,
        max_delay_seconds=settings.max_delay_seconds,
        floor_delay_seconds=settings.floor_delay_seconds,
        risk_level=_calc_risk(settings.min_delay_seconds),
        updated_at=settings.updated_at,
    )
