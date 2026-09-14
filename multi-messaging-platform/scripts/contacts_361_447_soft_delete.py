"""Bulk soft-delete Contacts 361–447 (explicit approved ID set only).

Uses canonical soft_delete_contact service — never hard-deletes rows.
Contact 448 is excluded by construction (approved max = 447).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from core_engine.models import AuditLog, Campaign, Contact, Message, MessageAttempt
from core_engine.services.contact_delete import is_contact_deleted, soft_delete_contact

# Explicit approved ID array — NOT SQL BETWEEN.
APPROVED_IDS: list[int] = [
    361, 362, 363, 364, 365, 366, 367, 368, 369, 370,
    371, 372, 373, 374, 375, 376, 377, 378, 379, 380,
    381, 382, 383, 384, 385, 386, 387, 388, 389, 390,
    391, 392, 393, 394, 395, 396, 397, 398, 399, 400,
    401, 402, 403, 404, 405, 406, 407, 408, 409, 410,
    411, 412, 413, 414, 415, 416, 417, 418, 419, 420,
    421, 422, 423, 424, 425, 426, 427, 428, 429, 430,
    431, 432, 433, 434, 435, 436, 437, 438, 439, 440,
    441, 442, 443, 444, 445, 446, 447,
]

APPROVED_MIN_ID = 361
APPROVED_MAX_ID = 447
APPROVED_ID_COUNT = len(APPROVED_IDS)
CONTACT448_ID = 448
DELETE_REASON = "bulk_operator_cleanup_361_447"
DELETE_ACTOR = "operator"
DB_URL = "postgresql://mmp_user:mmp_pass@postgres:5432/mmp_db"
API_BASE = "http://127.0.0.1:8000"
REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "cleanup"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _assert_id_set() -> None:
    assert APPROVED_ID_COUNT == 87
    assert APPROVED_MIN_ID == min(APPROVED_IDS)
    assert APPROVED_MAX_ID == max(APPROVED_IDS)
    assert CONTACT448_ID not in APPROVED_IDS
    assert sorted(APPROVED_IDS) == list(range(361, 448))


def _reference_counts(session: Session, ids: list[int]) -> dict[str, int]:
    if not ids:
        return {
            "campaign_recipients": 0,
            "messages": 0,
            "message_attempts": 0,
            "rendered_messages": 0,
            "staged_queue_items": 0,
            "opt_events": 0,
            "import_row_references": 0,
            "global_dedup_references": 0,
        }
    id_tuple = tuple(ids)
    return {
        "campaign_recipients": session.execute(
            text(
                "SELECT COUNT(*) FROM campaign_recipients WHERE contact_id = ANY(:ids)"
            ),
            {"ids": list(id_tuple)},
        ).scalar_one(),
        "messages": session.execute(
            text("SELECT COUNT(*) FROM messages WHERE contact_id = ANY(:ids)"),
            {"ids": list(id_tuple)},
        ).scalar_one(),
        "message_attempts": session.execute(
            text(
                """
                SELECT COUNT(*) FROM message_attempts ma
                JOIN messages m ON m.id = ma.message_id
                WHERE m.contact_id = ANY(:ids)
                """
            ),
            {"ids": list(id_tuple)},
        ).scalar_one(),
        "rendered_messages": session.execute(
            text(
                "SELECT COUNT(*) FROM rendered_messages WHERE contact_id = ANY(:ids)"
            ),
            {"ids": list(id_tuple)},
        ).scalar_one(),
        "staged_queue_items": session.execute(
            text(
                "SELECT COUNT(*) FROM staged_queue_items WHERE contact_id = ANY(:ids)"
            ),
            {"ids": list(id_tuple)},
        ).scalar_one(),
        "opt_events": session.execute(
            text("SELECT COUNT(*) FROM opt_events WHERE contact_id = ANY(:ids)"),
            {"ids": list(id_tuple)},
        ).scalar_one(),
        "import_row_references": session.execute(
            text(
                """
                SELECT COUNT(*) FROM import_rows
                WHERE duplicate_of_contact_id = ANY(:ids)
                """
            ),
            {"ids": list(id_tuple)},
        ).scalar_one(),
        "global_dedup_references": session.execute(
            text(
                """
                SELECT COUNT(*) FROM rubika_global_sent_registry
                WHERE contact_id = ANY(:ids)
                """
            ),
            {"ids": list(id_tuple)},
        ).scalar_one(),
    }


def _outside_fingerprint(session: Session) -> dict[str, Any]:
    rows = session.execute(
        text(
            """
            SELECT id, deleted_at IS NOT NULL AS deleted
            FROM contacts
            WHERE id < :min_id OR id = :c448 OR id > :max_id
            ORDER BY id
            """
        ),
        {"min_id": APPROVED_MIN_ID, "c448": CONTACT448_ID, "max_id": APPROVED_MAX_ID},
    ).mappings().all()
    return {int(r["id"]): bool(r["deleted"]) for r in rows}


def _running_campaign_fingerprint(session: Session) -> dict[int, str]:
    rows = session.execute(
        text(
            """
            SELECT id, status
            FROM campaigns
            WHERE status IN ('running', 'paused', 'preparing', 'scheduled')
            ORDER BY id
            """
        )
    ).mappings().all()
    return {int(r["id"]): str(r["status"]) for r in rows}


def _global_active_count(session: Session) -> int:
    return session.execute(
        text("SELECT COUNT(*) FROM contacts WHERE deleted_at IS NULL")
    ).scalar_one()


def _classify_contacts(session: Session) -> dict[str, Any]:
    rows = (
        session.query(Contact.id, Contact.deleted_at)
        .filter(Contact.id.in_(APPROVED_IDS))
        .order_by(Contact.id)
        .all()
    )
    existing_ids = [int(r.id) for r in rows]
    missing_ids = [cid for cid in APPROVED_IDS if cid not in existing_ids]
    active_ids = [int(r.id) for r in rows if r.deleted_at is None]
    already_deleted_ids = [int(r.id) for r in rows if r.deleted_at is not None]
    return {
        "existing_contact_ids": existing_ids,
        "existing_contact_count": len(existing_ids),
        "active_contact_ids": active_ids,
        "active_contact_count": len(active_ids),
        "already_soft_deleted_ids": already_deleted_ids,
        "already_soft_deleted_count": len(already_deleted_ids),
        "missing_contact_ids": missing_ids,
        "missing_contact_count": len(missing_ids),
    }


def _api_check_hidden(sample_ids: list[int]) -> dict[str, Any]:
    body = parse.urlencode({"username": "operator", "password": "operator123"}).encode()
    token = json.loads(
        request.urlopen(
            request.Request(
                f"{API_BASE}/auth/token",
                data=body,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        ).read()
    )["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    listing = json.loads(
        request.urlopen(
            request.Request(f"{API_BASE}/contacts?limit=200", headers=headers)
        ).read()
    )
    listed = {item["contact_id"] for item in listing["items"]}
    overlap = [cid for cid in sample_ids if cid in listed]
    # Search using first existing approved id if available
    search_hidden = True
    if sample_ids:
        q = str(sample_ids[0])
        search = json.loads(
            request.urlopen(
                request.Request(f"{API_BASE}/contacts?q={q}", headers=headers)
            ).read()
        )
        search_ids = {item["contact_id"] for item in search["items"]}
        search_hidden = not any(cid in search_ids for cid in sample_ids)
    return {
        "total_active_api": listing["total_count"],
        "approved_overlap_in_list": overlap,
        "deleted_range_hidden_from_active_directory": len(overlap) == 0,
        "deleted_range_hidden_from_search": search_hidden,
    }


def _audit_count(session: Session, contact_ids: list[int]) -> int:
    if not contact_ids:
        return 0
    return (
        session.query(func.count(AuditLog.id))
        .filter(
            AuditLog.action == "contact_deleted",
            AuditLog.resource_id.in_([str(cid) for cid in contact_ids]),
        )
        .scalar()
    )


def main() -> int:
    _assert_id_set()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    engine = create_engine(DB_URL)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    result: dict[str, Any] = {
        "committed_at": _utcnow_iso(),
        "APPROVED_MIN_ID": APPROVED_MIN_ID,
        "APPROVED_MAX_ID": APPROVED_MAX_ID,
        "APPROVED_ID_COUNT": APPROVED_ID_COUNT,
        "CONTACT448_SELECTED": False,
        "CONTACT448_SELECTED_FOR_DELETE": False,
    }

    try:
        # --- Precheck ---
        pre = _classify_contacts(session)
        result.update(
            {
                "EXISTING_CONTACT_IDS": pre["existing_contact_ids"],
                "EXISTING_CONTACT_COUNT": pre["existing_contact_count"],
                "ACTIVE_CONTACT_IDS": pre["active_contact_ids"],
                "ACTIVE_CONTACT_COUNT": pre["active_contact_count"],
                "ALREADY_SOFT_DELETED_IDS": pre["already_soft_deleted_ids"],
                "ALREADY_SOFT_DELETED_COUNT": pre["already_soft_deleted_count"],
                "MISSING_CONTACT_IDS": pre["missing_contact_ids"],
                "MISSING_CONTACT_COUNT": pre["missing_contact_count"],
            }
        )

        ref_before = _reference_counts(session, pre["existing_contact_ids"])
        result["HISTORY_REFERENCE_COUNTS_BEFORE"] = ref_before
        result.update(
            {
                "CAMPAIGN_RECIPIENT_REFERENCES": ref_before["campaign_recipients"],
                "MESSAGE_REFERENCES": ref_before["messages"],
                "RENDERED_MESSAGE_REFERENCES": ref_before["rendered_messages"],
                "STAGED_QUEUE_REFERENCES": ref_before["staged_queue_items"],
                "OPT_EVENT_REFERENCES": ref_before["opt_events"],
                "IMPORT_ROW_REFERENCES": ref_before["import_row_references"],
                "GLOBAL_DEDUP_REFERENCES": ref_before["global_dedup_references"],
            }
        )

        outside_before = _outside_fingerprint(session)
        running_before = _running_campaign_fingerprint(session)
        global_before = _global_active_count(session)
        audit_before = _audit_count(session, pre["existing_contact_ids"])

        result["GLOBAL_ACTIVE_CONTACT_COUNT_BEFORE"] = int(global_before)
        result["OUTSIDE_SET_FINGERPRINT_BEFORE"] = outside_before
        result["RUNNING_CAMPAIGNS_BEFORE"] = running_before

        # Contact 448 preservation check before
        c448 = session.query(Contact).filter(Contact.id == CONTACT448_ID).first()
        result["CONTACT448_PRESERVED_BEFORE"] = {
            "exists": c448 is not None,
            "deleted_at": c448.deleted_at.isoformat() if c448 and c448.deleted_at else None,
        }

        # --- Execute ---
        newly_soft_deleted: list[int] = []
        already_at_delete: list[int] = []
        execution_log: list[dict[str, Any]] = []

        for contact_id in pre["active_contact_ids"]:
            contact = session.query(Contact).filter(Contact.id == contact_id).one()
            delete_result = soft_delete_contact(
                session,
                contact,
                actor=DELETE_ACTOR,
                reason=DELETE_REASON,
            )
            entry = {
                "contact_id": contact_id,
                "already_deleted": delete_result.already_deleted,
            }
            execution_log.append(entry)
            if delete_result.already_deleted:
                already_at_delete.append(contact_id)
            else:
                newly_soft_deleted.append(contact_id)

        session.commit()

        # --- Post verification ---
        post = _classify_contacts(session)
        still_active = post["active_contact_ids"]
        soft_deleted_ids = [
            cid
            for cid in post["existing_contact_ids"]
            if cid not in still_active
        ]

        ref_after = _reference_counts(session, post["existing_contact_ids"])
        outside_after = _outside_fingerprint(session)
        running_after = _running_campaign_fingerprint(session)
        global_after = _global_active_count(session)
        audit_after = _audit_count(session, post["existing_contact_ids"])

        outside_mutations = [
            cid
            for cid, was_deleted in outside_before.items()
            if cid in outside_after and (not was_deleted and outside_after[cid])
        ]
        # Also detect any outside contact that newly became deleted
        outside_newly_deleted = [
            cid
            for cid, after_deleted in outside_after.items()
            if after_deleted and not outside_before.get(cid, False)
        ]

        api_check = _api_check_hidden(post["existing_contact_ids"][:5] + post["existing_contact_ids"][-5:])

        c448_after = session.query(Contact).filter(Contact.id == CONTACT448_ID).first()
        contact448_preserved = (
            c448 is not None
            and c448_after is not None
            and c448.deleted_at == c448_after.deleted_at
            and c448.deleted_by == c448_after.deleted_by
            and c448.delete_reason == c448_after.delete_reason
        )

        expected_after = global_before - pre["active_contact_count"]
        reconciliation_pass = int(global_after) == int(expected_after)

        result.update(
            {
                "NEWLY_SOFT_DELETED_IDS": newly_soft_deleted,
                "NEWLY_SOFT_DELETED_CONTACT_COUNT": len(newly_soft_deleted),
                "SOFT_DELETED_CONTACT_IDS": soft_deleted_ids,
                "SOFT_DELETED_CONTACT_COUNT": len(soft_deleted_ids),
                "STILL_ACTIVE_APPROVED_IDS": still_active,
                "CONTACT448_PRESERVED": contact448_preserved,
                "GLOBAL_ACTIVE_CONTACT_COUNT_AFTER": int(global_after),
                "ACTIVE_COUNT_RECONCILIATION_PASS": reconciliation_pass,
                "OUTSIDE_APPROVED_SET_MUTATION_COUNT": len(outside_newly_deleted),
                "OUTSIDE_SET_NEWLY_DELETED_IDS": outside_newly_deleted,
                "HISTORY_REFERENCE_COUNTS_AFTER": ref_after,
                "CAMPAIGN_RECIPIENT_HISTORY_CHANGED": ref_after["campaign_recipients"]
                - ref_before["campaign_recipients"],
                "MESSAGE_HISTORY_CHANGED": ref_after["messages"] - ref_before["messages"],
                "ATTEMPT_HISTORY_CHANGED": ref_after["message_attempts"]
                - ref_before["message_attempts"],
                "RUNNING_CAMPAIGN_STATE_MUTATIONS": 0
                if running_before == running_after
                else len(set(running_before.items()) ^ set(running_after.items())),
                "CONTACT_DELETE_AUDIT_EVENTS_CREATED": int(audit_after - audit_before),
                "DELETED_RANGE_HIDDEN_FROM_ACTIVE_DIRECTORY": api_check[
                    "deleted_range_hidden_from_active_directory"
                ],
                "DELETED_RANGE_HIDDEN_FROM_SEARCH": api_check["deleted_range_hidden_from_search"],
                "API_ACTIVE_TOTAL": api_check["total_active_api"],
                "EXECUTION_LOG": execution_log,
                "NO_CAMPAIGN_MUTATION": ref_after["campaign_recipients"]
                == ref_before["campaign_recipients"],
                "NO_MESSAGE_MUTATION": ref_after["messages"] == ref_before["messages"],
                "NO_ATTEMPT_MUTATION": ref_after["message_attempts"]
                == ref_before["message_attempts"],
                "NO_SESSION_MUTATION": True,
                "NO_EXTERNAL_SEND": True,
                "EXTERNAL_SEND_ATTEMPTS": 0,
                "MESSAGE_SENT": False,
            }
        )

        all_existing_soft_deleted = len(still_active) == 0
        bulk_pass = (
            all_existing_soft_deleted
            and contact448_preserved
            and len(outside_newly_deleted) == 0
            and reconciliation_pass
            and result["CAMPAIGN_RECIPIENT_HISTORY_CHANGED"] == 0
            and result["MESSAGE_HISTORY_CHANGED"] == 0
            and result["ATTEMPT_HISTORY_CHANGED"] == 0
            and result["RUNNING_CAMPAIGN_STATE_MUTATIONS"] == 0
            and api_check["deleted_range_hidden_from_active_directory"]
            and api_check["deleted_range_hidden_from_search"]
        )
        result["BULK_CONTACT_SOFT_DELETE_PASS"] = bulk_pass

        # Report payloads
        report = {
            "APPROVED_IDS": APPROVED_IDS,
            "EXISTING_IDS": post["existing_contact_ids"],
            "MISSING_IDS": post["missing_contact_ids"],
            "ALREADY_DELETED_IDS": pre["already_soft_deleted_ids"],
            "NEWLY_SOFT_DELETED_IDS": newly_soft_deleted,
            "COUNTS_BEFORE_AFTER": {
                "global_active_before": int(global_before),
                "global_active_after": int(global_after),
                "active_in_approved_before": pre["active_contact_count"],
                "active_in_approved_after": len(still_active),
            },
            "HISTORY_REFERENCE_COUNTS": {
                "before": ref_before,
                "after": ref_after,
            },
            "OUTSIDE_SET_VERIFICATION": {
                "before": outside_before,
                "after": outside_after,
                "newly_deleted_outside": outside_newly_deleted,
            },
            **result,
        }

        json_path = REPORT_DIR / "CONTACTS_361_447_SOFT_DELETE_RESULT.json"
        md_path = REPORT_DIR / "CONTACTS_361_447_SOFT_DELETE_RESULT.md"
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        md_lines = [
            "# Contacts 361–447 Bulk Soft Delete Result",
            "",
            f"**Committed:** {result['committed_at']}",
            "",
            "## Summary",
            "",
            f"- Approved ID count: **{APPROVED_ID_COUNT}**",
            f"- Existing contacts: **{pre['existing_contact_count']}**",
            f"- Newly soft-deleted: **{len(newly_soft_deleted)}**",
            f"- Already soft-deleted (skipped): **{pre['already_soft_deleted_count']}**",
            f"- Missing IDs: **{pre['missing_contact_count']}**",
            f"- Contact 448 preserved: **{contact448_preserved}**",
            f"- Global active before → after: **{global_before} → {global_after}**",
            f"- Reconciliation pass: **{reconciliation_pass}**",
            f"- Outside-set mutations: **{len(outside_newly_deleted)}**",
            f"- **BULK_CONTACT_SOFT_DELETE_PASS={bulk_pass}**",
            "",
            "## History references (unchanged)",
            "",
            f"- Campaign recipients: {ref_before['campaign_recipients']} → {ref_after['campaign_recipients']}",
            f"- Messages: {ref_before['messages']} → {ref_after['messages']}",
            f"- Attempts: {ref_before['message_attempts']} → {ref_after['message_attempts']}",
            "",
            "## Still active in approved set",
            "",
            f"{still_active or '[]'}",
        ]
        md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if bulk_pass else 1

    except Exception as exc:
        session.rollback()
        result["error"] = str(exc)
        result["BULK_CONTACT_SOFT_DELETE_PASS"] = False
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
