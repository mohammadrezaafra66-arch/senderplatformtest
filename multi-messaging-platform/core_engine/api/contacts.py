"""API endpoints for operator browsing existing contacts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from core_engine.api.schemas import (
    ContactDeleteResponse,
    ContactListItemResponse,
    ContactsListResponse,
    ContactsSearchResponse,
    ContactSearchItemResponse,
)
from core_engine.database import get_db
from core_engine.models import (
    CampaignRecipient,
    ConsentStatus,
    Contact,
    ImportBatch,
    ImportRow,
    RoleType,
)
from core_engine.services.contact_delete import is_contact_deleted, soft_delete_contact
from core_engine.services.rbac import requires_role

router = APIRouter(prefix="/contacts", tags=["contacts"])

ContactSort = Literal["created_at_desc", "created_at_asc", "name_asc", "name_desc"]


def _active_contacts_filter(query):
    return query.filter(Contact.deleted_at.is_(None))


def _contact_eligibility(contact: Contact) -> tuple[bool, str | None]:
    if is_contact_deleted(contact):
        return False, "deleted"
    if contact.blacklisted:
        return False, "blacklisted"
    if contact.consent_status == ConsentStatus.BLOCKED.value:
        return False, "consent_blocked"
    return True, None


def _contact_display_phone(contact: Contact) -> str:
    return contact.phone_e164 or contact.phone


def _build_search_item(contact: Contact) -> ContactSearchItemResponse:
    eligible, ineligible_reason = _contact_eligibility(contact)
    return ContactSearchItemResponse(
        contact_id=contact.id,
        first_name=contact.first_name,
        last_name=contact.last_name,
        full_name=contact.full_name,
        phone=_contact_display_phone(contact),
        consent_status=contact.consent_status,
        blacklisted=bool(contact.blacklisted),
        eligible=eligible,
        ineligible_reason=ineligible_reason,
    )


def _import_counts_by_contact(db: Session, contact_ids: list[int]) -> dict[int, int]:
    if not contact_ids:
        return {}
    rows = (
        db.query(ImportRow.duplicate_of_contact_id, func.count(ImportRow.id))
        .filter(ImportRow.duplicate_of_contact_id.in_(contact_ids))
        .group_by(ImportRow.duplicate_of_contact_id)
        .all()
    )
    return {int(contact_id): int(count) for contact_id, count in rows}


def _campaign_counts_by_contact(db: Session, contact_ids: list[int]) -> dict[int, int]:
    if not contact_ids:
        return {}
    rows = (
        db.query(
            CampaignRecipient.contact_id,
            func.count(func.distinct(CampaignRecipient.campaign_id)),
        )
        .filter(CampaignRecipient.contact_id.in_(contact_ids))
        .group_by(CampaignRecipient.contact_id)
        .all()
    )
    return {int(contact_id): int(count) for contact_id, count in rows}


def _build_list_item(
    contact: Contact,
    *,
    duplicate_import_count: int,
    campaign_count: int,
) -> ContactListItemResponse:
    eligible, ineligible_reason = _contact_eligibility(contact)
    import_count = duplicate_import_count + (1 if contact.source_import_id is not None else 0)
    source_batch = contact.source_import
    return ContactListItemResponse(
        contact_id=contact.id,
        first_name=contact.first_name,
        last_name=contact.last_name,
        full_name=contact.full_name,
        phone=_contact_display_phone(contact),
        consent_status=contact.consent_status,
        blacklisted=bool(contact.blacklisted),
        eligible=eligible,
        ineligible_reason=ineligible_reason,
        created_at=contact.created_at,
        source_import_id=contact.source_import_id,
        source_import_file_name=(
            source_batch.original_file_name or source_batch.file_name if source_batch else None
        ),
        source_imported_at=(
            source_batch.committed_at or source_batch.created_at if source_batch else None
        ),
        import_count=import_count,
        campaign_count=campaign_count,
    )


def _apply_contact_sort(query, sort: ContactSort):
    name_expr = func.coalesce(Contact.full_name, Contact.first_name, Contact.phone)
    if sort == "created_at_asc":
        return query.order_by(Contact.created_at.asc(), Contact.id.asc())
    if sort == "name_asc":
        return query.order_by(name_expr.asc(), Contact.id.asc())
    if sort == "name_desc":
        return query.order_by(name_expr.desc(), Contact.id.desc())
    return query.order_by(Contact.created_at.desc(), Contact.id.desc())


@router.get("", response_model=ContactsListResponse)
def list_contacts(
    q: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    import_batch_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort: ContactSort = "created_at_desc",
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str],
        Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR, RoleType.VIEWER)),
    ] = None,
):
    """List active canonical contacts in global scope with optional filters."""

    query = _active_contacts_filter(
        db.query(Contact).options(joinedload(Contact.source_import))
    )

    needle = (q or "").strip()
    if needle:
        like = f"%{needle}%"
        query = query.filter(
            or_(
                Contact.phone.ilike(like),
                Contact.phone_e164.ilike(like),
                Contact.first_name.ilike(like),
                Contact.last_name.ilike(like),
                Contact.full_name.ilike(like),
                Contact.channel_handle.ilike(like),
            )
        )

    if import_batch_id is not None:
        batch = db.query(ImportBatch.id).filter(ImportBatch.id == import_batch_id).first()
        if not batch:
            raise HTTPException(status_code=404, detail="Import batch not found.")
        duplicate_contact_ids = (
            db.query(ImportRow.duplicate_of_contact_id)
            .filter(
                ImportRow.batch_id == import_batch_id,
                ImportRow.duplicate_of_contact_id.isnot(None),
            )
            .subquery()
        )
        query = query.filter(
            or_(
                Contact.source_import_id == import_batch_id,
                Contact.id.in_(duplicate_contact_ids),
            )
        )

    if date_from is not None:
        query = query.filter(Contact.created_at >= date_from)
    if date_to is not None:
        query = query.filter(Contact.created_at <= date_to)

    total_count = query.count()
    contacts = _apply_contact_sort(query, sort).limit(limit).offset(offset).all()

    contact_ids = [int(c.id) for c in contacts]
    import_counts = _import_counts_by_contact(db, contact_ids)
    campaign_counts = _campaign_counts_by_contact(db, contact_ids)

    items = [
        _build_list_item(
            contact,
            duplicate_import_count=import_counts.get(int(contact.id), 0),
            campaign_count=campaign_counts.get(int(contact.id), 0),
        )
        for contact in contacts
    ]

    return ContactsListResponse(
        items=items,
        total_count=total_count,
        limit=limit,
        offset=offset,
    )


@router.post("/{contact_id}/delete", response_model=ContactDeleteResponse)
def delete_contact_endpoint(
    contact_id: int,
    db: Annotated[Session, Depends(get_db)] = None,
    current_user: Annotated[
        dict[str, str],
        Depends(requires_role(RoleType.ADMIN, RoleType.OPERATOR)),
    ] = None,
):
    """Soft-delete a contact. Idempotent. Preserves campaign/message history."""

    contact = db.query(Contact).filter(Contact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found.")

    result = soft_delete_contact(db, contact, actor=current_user["username"])
    db.commit()
    db.refresh(contact)

    return ContactDeleteResponse(
        success=True,
        contact_id=result.contact_id,
        already_deleted=result.already_deleted,
        deleted_at=result.deleted_at,
        message=(
            "Contact already deleted."
            if result.already_deleted
            else "Contact deleted successfully."
        ),
    )


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
    """Search active contacts by name/phone. Eligibility is computed server-side."""

    like = f"%{q.strip()}%"

    base_query = _active_contacts_filter(db.query(Contact)).filter(
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

    items = [_build_search_item(c) for c in contacts]

    return ContactsSearchResponse(
        items=items,
        total_count=total_count,
        limit=limit,
        offset=offset,
    )
