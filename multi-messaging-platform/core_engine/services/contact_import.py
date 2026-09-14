"""Import-time contact upsert. One normalized phone, one contact, additive tags."""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core_engine.models import ConsentStatus, Contact
from core_engine.services.contact_delete import (
    is_contact_deleted,
    reactivate_deleted_contact_from_import,
)
from core_engine.services.contact_tags import fill_empty_name, merge_tags, read_contact_tags


def find_contact_by_phone(db: Session, phone_e164: str) -> Contact | None:
    return (
        db.query(Contact)
        .filter(Contact.phone_e164 == phone_e164)
        .first()
    )


def apply_import_to_contact(
    db: Session,
    *,
    phone_e164: str,
    first_name: str | None,
    last_name: str | None,
    tags: Any,
    import_batch_id: int | None = None,
    import_row_id: int | None = None,
    telegram_hint: str | None = None,
    locale: str | None = None,
    extra_variables: dict | None = None,
    channel_handle: str | None = None,
) -> tuple[Contact, str, list[str]]:
    """Create or merge. Returns (contact, action, tags_added).

    action is created | merged | reactivated.
    Concurrent inserts of the same phone collapse via phone_e164 uniqueness.
    """
    incoming = read_contact_tags(tags)
    existing = find_contact_by_phone(db, phone_e164)
    if existing is not None:
        return _merge_existing(
            existing,
            first_name=first_name,
            last_name=last_name,
            incoming=incoming,
            import_batch_id=import_batch_id,
            import_row_id=import_row_id,
            telegram_hint=telegram_hint,
            locale=locale,
            extra_variables=extra_variables,
        )

    contact = Contact(
        first_name=first_name,
        last_name=last_name,
        phone=phone_e164,
        phone_e164=phone_e164,
        channel_handle=channel_handle,
        telegram_hint=telegram_hint,
        locale=locale or "fa-IR",
        consent_status=ConsentStatus.ALLOWED.value,
        blacklisted=False,
        tags=incoming,
        extra_variables=extra_variables or {},
        source_import_id=import_batch_id,
        source_import_row_id=import_row_id,
    )
    fill_empty_name(contact, first_name=first_name, last_name=last_name)
    try:
        with db.begin_nested():
            db.add(contact)
            db.flush()
    except IntegrityError:
        raced = find_contact_by_phone(db, phone_e164)
        if raced is None:
            raise
        return _merge_existing(
            raced,
            first_name=first_name,
            last_name=last_name,
            incoming=incoming,
            import_batch_id=import_batch_id,
            import_row_id=import_row_id,
            telegram_hint=telegram_hint,
            locale=locale,
            extra_variables=extra_variables,
        )
    return contact, "created", list(incoming)


def _merge_existing(
    contact: Contact,
    *,
    first_name: str | None,
    last_name: str | None,
    incoming: list[str],
    import_batch_id: int | None,
    import_row_id: int | None,
    telegram_hint: str | None,
    locale: str | None,
    extra_variables: dict | None,
) -> tuple[Contact, str, list[str]]:
    if is_contact_deleted(contact):
        reactivate_deleted_contact_from_import(
            contact,
            import_batch_id=import_batch_id or 0,
            import_row_id=import_row_id or 0,
            normalized={
                "first_name": first_name,
                "last_name": last_name,
                "telegram_hint": telegram_hint,
                "locale": locale,
                "extra_variables": extra_variables or {},
            },
        )
        action = "reactivated"
    else:
        action = "merged"
    before = set(read_contact_tags(contact.tags))
    contact.tags = merge_tags(contact.tags, incoming)
    added = [tag for tag in contact.tags if tag not in before]
    fill_empty_name(contact, first_name=first_name, last_name=last_name)
    return contact, action, added
