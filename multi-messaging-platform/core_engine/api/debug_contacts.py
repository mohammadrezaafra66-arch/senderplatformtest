"""Phase 4 debug endpoints for contact import and preview."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core_engine.api.utf8_json import utf8_json_response
from core_engine.database import get_db
from core_engine.models import Campaign, Contact
from core_engine.schemas.phase4 import (
    ContactDebugResponse,
    ContactImportRequest,
    ContactImportResultResponse,
)
from core_engine.services.phase4_utils import (
    build_full_name,
    normalize_consent_status,
    normalize_phone,
)
from core_engine.services.safety_guard import SafetyViolationError, assert_phase_4_staging_safe

router = APIRouter(prefix="/debug/contacts", tags=["debug-contacts"])


def _contact_to_debug_response(contact: Contact) -> ContactDebugResponse:
    return ContactDebugResponse.model_validate(contact)


def _item_raw_payload(item: Any) -> dict[str, Any]:
    payload = item.model_dump(exclude_none=False)
    if item.raw_payload is not None:
        return item.raw_payload
    return payload


def _count_consent(contacts: list[Contact]) -> tuple[int, int, int]:
    allowed = blocked = unknown = 0
    for contact in contacts:
        if contact.consent_status == "allowed":
            allowed += 1
        elif contact.consent_status == "blocked":
            blocked += 1
        else:
            unknown += 1
    return allowed, blocked, unknown


@router.post("/import-json")
def debug_import_contacts_json(
    payload: ContactImportRequest,
    db: Annotated[Session, Depends(get_db)],
):
    try:
        assert_phase_4_staging_safe()
    except SafetyViolationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    campaign = db.query(Campaign).filter(Campaign.id == payload.campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    existing_phones = {
        phone
        for (phone,) in db.query(Contact.phone)
        .filter(Contact.campaign_id == payload.campaign_id)
        .all()
    }

    seen_phones_in_request: set[str] = set()
    skipped_duplicates: list[dict[str, Any]] = []
    invalid_items: list[dict[str, Any]] = []
    imported_contacts: list[Contact] = []

    for item in payload.contacts:
        item_payload = _item_raw_payload(item)

        if not item.phone or not str(item.phone).strip():
            invalid_items.append({**item_payload, "error": "phone is required and must be non-empty"})
            continue

        try:
            normalized_phone = normalize_phone(item.phone)
        except ValueError as exc:
            invalid_items.append({**item_payload, "error": str(exc)})
            continue

        try:
            consent = normalize_consent_status(item.consent_status)
        except ValueError as exc:
            invalid_items.append({**item_payload, "error": str(exc)})
            continue

        if normalized_phone in seen_phones_in_request:
            skipped_duplicates.append(
                {
                    **item_payload,
                    "phone": normalized_phone,
                    "reason": "duplicate in request",
                }
            )
            continue

        if normalized_phone in existing_phones:
            skipped_duplicates.append(
                {
                    **item_payload,
                    "phone": normalized_phone,
                    "reason": "duplicate in campaign",
                }
            )
            continue

        seen_phones_in_request.add(normalized_phone)

        contact = Contact(
            campaign_id=payload.campaign_id,
            first_name=item.first_name,
            last_name=item.last_name,
            full_name=build_full_name(item.first_name, item.last_name),
            phone=normalized_phone,
            channel_handle=item.channel_handle,
            consent_status=consent,
            tags=item.tags,
            raw_payload=item_payload,
        )

        global_phone_owner = (
            db.query(Contact.id)
            .filter(Contact.phone_e164 == normalized_phone)
            .first()
        )
        if global_phone_owner is None:
            contact.phone_e164 = normalized_phone

        db.add(contact)
        try:
            db.commit()
            db.refresh(contact)
        except IntegrityError:
            db.rollback()
            skipped_duplicates.append(
                {
                    **item_payload,
                    "phone": normalized_phone,
                    "reason": "duplicate in campaign",
                }
            )
            existing_phones = {
                phone
                for (phone,) in db.query(Contact.phone)
                .filter(Contact.campaign_id == payload.campaign_id)
                .all()
            }
            continue

        imported_contacts.append(contact)
        existing_phones.add(normalized_phone)

    allowed_count, blocked_count, unknown_count = _count_consent(imported_contacts)
    result = ContactImportResultResponse(
        campaign_id=payload.campaign_id,
        received_count=len(payload.contacts),
        imported_count=len(imported_contacts),
        duplicate_count=len(skipped_duplicates),
        invalid_count=len(invalid_items),
        allowed_count=allowed_count,
        blocked_count=blocked_count,
        unknown_count=unknown_count,
        skipped_duplicates=skipped_duplicates,
        invalid_items=invalid_items,
        contacts=[_contact_to_debug_response(contact) for contact in imported_contacts],
    )
    return utf8_json_response(result.model_dump())


@router.get("/latest")
def debug_latest_contacts(
    db: Annotated[Session, Depends(get_db)],
    campaign_id: int = Query(..., ge=1),
    limit: int = Query(default=20, ge=1, le=100),
):
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found.")

    contacts = (
        db.query(Contact)
        .filter(Contact.campaign_id == campaign_id)
        .order_by(Contact.id.desc())
        .limit(limit)
        .all()
    )
    items = [_contact_to_debug_response(contact).model_dump() for contact in contacts]
    return utf8_json_response(items)
