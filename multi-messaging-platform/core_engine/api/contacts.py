"""API endpoints for operator browsing existing contacts."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from core_engine.api.schemas import ContactsSearchResponse, ContactSearchItemResponse
from core_engine.database import get_db
from core_engine.models import ConsentStatus, Contact, RoleType
from core_engine.services.rbac import requires_role

router = APIRouter(prefix="/contacts", tags=["contacts"])


def _contact_eligibility(contact: Contact) -> tuple[bool, str | None]:
    if contact.blacklisted:
        return False, "blacklisted"
    if contact.consent_status == ConsentStatus.BLOCKED.value:
        return False, "consent_blocked"
    return True, None


@router.get("/search", response_model=ContactsSearchResponse)
def search_contacts(
    q: Annotated[str, Query(min_length=1, max_length=80)],
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str],
        Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR)),
    ] = None,
):
    """Search contacts by name/phone. Eligibility is computed server-side."""

    like = f"%{q.strip()}%"

    base_query = db.query(Contact).filter(
        or_(
            Contact.phone.ilike(like),
            Contact.phone_e164.ilike(like),
            Contact.first_name.ilike(like),
            Contact.last_name.ilike(like),
            Contact.full_name.ilike(like),
        )
    )

    total_count = base_query.count()
    contacts = (
        base_query.order_by(Contact.id.desc()).limit(limit).offset(offset).all()
    )

    items: list[ContactSearchItemResponse] = []
    for c in contacts:
        eligible, ineligible_reason = _contact_eligibility(c)
        normalized_phone = c.phone_e164 or c.phone
        items.append(
            ContactSearchItemResponse(
                contact_id=c.id,
                first_name=c.first_name,
                last_name=c.last_name,
                full_name=c.full_name,
                phone=normalized_phone,
                consent_status=c.consent_status,
                blacklisted=bool(c.blacklisted),
                eligible=eligible,
                ineligible_reason=ineligible_reason,
            )
        )

    return ContactsSearchResponse(
        items=items,
        total_count=total_count,
        limit=limit,
        offset=offset,
    )

