"""Webhook — دریافت پیام از بله و ثبت خودکار chat_id در contacts."""

from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.orm import Session
from core_engine.database import get_db
import logging

router = APIRouter(prefix="/webhooks/bale", tags=["bale-webhook"])
logger = logging.getLogger("bale.webhook")


@router.post("/{account_id}")
async def receive_bale_update(
    account_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    message = body.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")

    if not chat_id:
        return {"ok": True, "status": "no_chat_id"}

    user = message.get("from") or {}
    first_name = user.get("first_name", "") or ""
    username = user.get("username", "") or ""
    phone = user.get("phone_number", "") or ""

    from core_engine.models import Contact
    existing = db.query(Contact).filter(
        Contact.phone == str(chat_id)
    ).first()

    if not existing:
        contact = Contact(
            phone=str(chat_id),
            first_name=first_name,
            notes=f"بله | username: @{username}" if username else "بله auto-registered",
        )
        db.add(contact)
        db.commit()
        logger.info(f"Bale new contact: {chat_id} ({first_name})")
        return {"ok": True, "status": "registered", "chat_id": chat_id}

    return {"ok": True, "status": "already_exists"}
