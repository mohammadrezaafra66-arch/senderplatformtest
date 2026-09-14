"""Safe soft-delete for canonical Contacts.

Deleting a contact hides it from the global directory and prevents new campaign
selection. Historical campaign recipients, messages, and attempts are preserved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from core_engine.models import Contact
from core_engine.services.audit_service import record_audit

ERROR_CONTACT_DELETED = "CONTACT_DELETED"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_contact_deleted(contact: Contact | None) -> bool:
    return contact is not None and contact.deleted_at is not None


def is_contact_active(contact: Contact | None) -> bool:
    return contact is not None and contact.deleted_at is None


@dataclass
class ContactDeleteResult:
    contact_id: int
    already_deleted: bool
    deleted_at: datetime | None
    details: dict[str, Any]


def soft_delete_contact(
    db: Session,
    contact: Contact,
    *,
    actor: str,
    reason: str | None = None,
) -> ContactDeleteResult:
    """Soft-delete a contact. Idempotent."""

    if is_contact_deleted(contact):
        return ContactDeleteResult(
            contact_id=int(contact.id),
            already_deleted=True,
            deleted_at=contact.deleted_at,
            details={"message": "Contact already deleted."},
        )

    now = utcnow()
    contact.deleted_at = now
    contact.deleted_by = actor
    contact.delete_reason = reason
    contact.updated_at = now

    record_audit(
        db,
        actor,
        "contact_deleted",
        "contact",
        str(contact.id),
        {
            "contact_id": int(contact.id),
            "deleted_at": now.isoformat(),
        },
    )

    return ContactDeleteResult(
        contact_id=int(contact.id),
        already_deleted=False,
        deleted_at=now,
        details={"message": "Contact deleted successfully."},
    )


def reactivate_deleted_contact_from_import(
    contact: Contact,
    *,
    import_batch_id: int,
    import_row_id: int,
    normalized: dict[str, Any],
    actor: str | None = None,
) -> None:
    """Restore a previously deleted contact when the same phone is re-imported."""

    contact.deleted_at = None
    contact.deleted_by = None
    contact.delete_reason = None
    contact.source_import_id = import_batch_id
    contact.source_import_row_id = import_row_id
    contact.updated_at = utcnow()

    if normalized.get("first_name"):
        contact.first_name = normalized.get("first_name")
    if normalized.get("last_name"):
        contact.last_name = normalized.get("last_name")
    if normalized.get("first_name") or normalized.get("last_name"):
        parts = [contact.first_name, contact.last_name]
        contact.full_name = " ".join(p for p in parts if p and str(p).strip()) or contact.full_name

    bale_phone = normalized.get("chat_id") or normalized.get("phone_bale") or ""
    if str(bale_phone).strip():
        contact.channel_handle = str(bale_phone).strip()
    if normalized.get("telegram_hint"):
        contact.telegram_hint = normalized.get("telegram_hint")
    if normalized.get("locale"):
        contact.locale = normalized.get("locale")
    if normalized.get("extra_variables"):
        contact.extra_variables = normalized.get("extra_variables") or contact.extra_variables
