"""Opt-in / opt-out and blacklist consent checks."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from core_engine.models import Contact, OptEvent, PlatformType
from core_engine.services.audit_service import record_audit

logger = logging.getLogger(__name__)


def _parse_platform(platform: str | None) -> PlatformType | None:
    if not platform:
        return None
    normalized = str(platform).strip().lower()
    try:
        return PlatformType(normalized)
    except ValueError:
        return None


def _latest_event_opted_in(
    db: Session,
    contact_id: int,
    platform: str | None = None,
) -> bool:
    platform_enum = _parse_platform(platform)
    events = (
        db.query(OptEvent)
        .filter(OptEvent.contact_id == contact_id)
        .order_by(OptEvent.timestamp.desc(), OptEvent.id.desc())
        .all()
    )
    if not events:
        return True

    for event in events:
        if event.channel is None or event.channel == platform_enum:
            return event.opted_in

    return True


def record_opt_in(
    db: Session,
    contact_id: int,
    platform: str | None = None,
    reason: str = "",
    *,
    username: str = "system",
) -> OptEvent:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise ValueError(f"Contact {contact_id} not found.")

    try:
        event = OptEvent(
            contact_id=contact_id,
            opted_in=True,
            channel=_parse_platform(platform),
            reason=reason or None,
        )
        contact.blacklisted = False
        db.add(event)
        db.flush()
        record_audit(
            db,
            username,
            "record_opt_in",
            "contact",
            str(contact_id),
            {"platform": platform, "reason": reason},
        )
        return event
    except Exception:
        if hasattr(db, "rollback"):
            db.rollback()
        logger.exception("record_opt_in failed for contact %s", contact_id)
        raise


def record_opt_out(
    db: Session,
    contact_id: int,
    platform: str | None = None,
    reason: str = "",
    *,
    username: str = "system",
) -> OptEvent:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise ValueError(f"Contact {contact_id} not found.")

    try:
        event = OptEvent(
            contact_id=contact_id,
            opted_in=False,
            channel=_parse_platform(platform),
            reason=reason or None,
        )
        contact.blacklisted = True
        db.add(event)
        db.flush()
        record_audit(
            db,
            username,
            "record_opt_out",
            "contact",
            str(contact_id),
            {"platform": platform, "reason": reason},
        )
        return event
    except Exception:
        if hasattr(db, "rollback"):
            db.rollback()
        logger.exception("record_opt_out failed for contact %s", contact_id)
        raise


def has_opted_in(
    db: Session,
    contact_id: int,
    platform: str | None = None,
) -> bool:
    contact = db.get(Contact, contact_id)
    if contact is None:
        return False
    if contact.blacklisted:
        return False
    return _latest_event_opted_in(db, contact_id, platform)


def get_consent_block_reason(
    db: Session,
    contact_id: int,
    platform: str | None = None,
) -> str | None:
    """Return block reason string or None when sending is allowed."""
    contact = db.get(Contact, contact_id)
    if contact is None:
        return None
    if not _latest_event_opted_in(db, contact_id, platform):
        return "opted_out"
    if contact.blacklisted:
        return "blacklisted"
    return None
