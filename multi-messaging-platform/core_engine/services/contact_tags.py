"""Canonical contact tags and phone identity for import merge and campaign audience.

Phone identity reuses ExcelProcessor.normalize_phone. Do not add a second normalizer.
Tags are a JSON list on contacts.tags. Import is additive. Manual edit may replace.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import cast, or_, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Query, Session

from core_engine.models import ConsentStatus, Contact
from core_engine.services.contact_delete import is_contact_deleted
from core_engine.services.excel_processor import ExcelProcessor

_TAG_SPLIT = re.compile(r"[,،;|]+")
_PROCESSOR = ExcelProcessor()


def canonical_phone(value: str | int | float | None) -> str | None:
    """Canonical contact identity. Equivalent Iranian mobile forms collapse to +989…"""
    return _PROCESSOR.normalize_phone(value)


def normalize_tag(value: Any) -> str | None:
    """Trim only. Empty is rejected. Case is preserved (VIP != vip)."""
    if value is None:
        return None
    text = str(value).replace("\u200c", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    return text


def parse_tag_cell(value: Any) -> list[str]:
    """Split a cell on comma / Persian comma / semicolon / pipe. No other separators."""
    if value is None:
        return []
    if isinstance(value, list):
        parts: list[Any] = value
    else:
        text = str(value).strip()
        if not text:
            return []
        parts = _TAG_SPLIT.split(text)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tag = normalize_tag(part)
        if tag is None or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def read_contact_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = raw.get("items") or raw.get("tags") or []
    if isinstance(raw, str):
        return parse_tag_cell(raw)
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = normalize_tag(item)
        if tag is None or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def merge_tags(existing: Any, incoming: Any) -> list[str]:
    """Additive union. Existing order is kept. New tags append once."""
    merged = read_contact_tags(existing)
    seen = set(merged)
    for tag in parse_tag_cell(incoming) if not isinstance(incoming, list) else read_contact_tags(incoming):
        if tag not in seen:
            seen.add(tag)
            merged.append(tag)
    return merged


def replace_tags(incoming: Any) -> list[str]:
    """Explicit operator replace. Not used by import."""
    return read_contact_tags(incoming if isinstance(incoming, list) else parse_tag_cell(incoming))


def fill_empty_name(contact: Contact, *, first_name: str | None, last_name: str | None) -> None:
    """Do not overwrite a non-empty operator-maintained name. Fill blanks only."""
    if not (contact.first_name or "").strip() and first_name:
        contact.first_name = first_name
    if not (contact.last_name or "").strip() and last_name:
        contact.last_name = last_name
    if not (contact.full_name or "").strip():
        joined = " ".join(part for part in (contact.first_name, contact.last_name) if part)
        if joined:
            contact.full_name = joined


def audience_exclusion(contact: Contact) -> str | None:
    if is_contact_deleted(contact):
        return "deleted"
    if contact.blacklisted:
        return "blacklisted"
    if contact.consent_status == ConsentStatus.BLOCKED.value:
        return "consent_blocked"
    if not (contact.phone_e164 or contact.phone or "").strip():
        return "invalid_phone"
    return None


def filter_eligible(contacts: list[Contact]) -> tuple[list[Contact], list[tuple[Contact, str]]]:
    eligible: list[Contact] = []
    skipped: list[tuple[Contact, str]] = []
    seen: set[int] = set()
    for contact in contacts:
        if int(contact.id) in seen:
            continue
        reason = audience_exclusion(contact)
        if reason:
            skipped.append((contact, reason))
            continue
        seen.add(int(contact.id))
        eligible.append(contact)
    return eligible, skipped


def apply_tag_filter(query: Query, tags: list[str], match: str) -> Query:
    normalized = read_contact_tags(tags)
    if not normalized:
        return query.filter(False)
    column = cast(Contact.tags, JSONB)
    mode = (match or "any").strip().lower()
    if mode == "all":
        return query.filter(column.contains(cast(normalized, JSONB)))
    clauses = [column.contains(cast([tag], JSONB)) for tag in normalized]
    return query.filter(or_(*clauses))


def list_known_tags(db: Session) -> list[str]:
    """Distinct operator-visible tags. Skips deleted contacts and non-array JSON."""
    rows = db.execute(
        text(
            """
            SELECT DISTINCT btrim(elem) AS tag
            FROM contacts
            CROSS JOIN LATERAL jsonb_array_elements_text(
                CASE
                    WHEN jsonb_typeof(COALESCE(tags::jsonb, '[]'::jsonb)) = 'array'
                    THEN COALESCE(tags::jsonb, '[]'::jsonb)
                    ELSE '[]'::jsonb
                END
            ) AS elem
            WHERE deleted_at IS NULL
              AND btrim(elem) <> ''
            ORDER BY tag
            """
        )
    ).fetchall()
    return [str(row[0]) for row in rows if row[0]]


def resolve_tag_audience(
    db: Session,
    tags: list[str],
    *,
    match: str = "any",
) -> tuple[list[Contact], list[tuple[Contact, str]]]:
    query = apply_tag_filter(db.query(Contact), tags, match).order_by(Contact.id.asc())
    rows = query.all()
    return filter_eligible(rows)
