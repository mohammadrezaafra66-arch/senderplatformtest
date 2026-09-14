"""READ-ONLY forensic diagnosis for owner UI campaigns. No writes."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from core_engine.database import SessionLocal
from core_engine.models import (
    Account,
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    ChannelSession,
    Contact,
    Message,
    MessageAttempt,
    PlatformType,
    RubikaAccountPool,
    RubikaSenderSchedule,
    StagedQueueItem,
)
from core_engine.services.account_session_wiring import evaluate_account_session_readiness
from core_engine.services.campaign_preflight import evaluate_campaign_send_preflight
from core_engine.services.redis_client import get_redis_client
from core_engine.services.rubika_preflight import evaluate_rubika_send_preflight
from workers.pool_health import has_active_worker_coverage
from workers.redis_keys import queue_key, worker_account_coverage_key
from workers.rubika_account_pool import resolve_current_phase

OUT = Path("/tmp/r10_forensic_out/OWNER_CAMPAIGN_DIAGNOSIS.json")


def _iso(v):
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _mask(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 6:
        return "***"
    return f"{digits[:3]}***{digits[-3:]}"


async def main() -> None:
    db = SessionLocal()
    out: dict = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "mode": "READ_ONLY",
        "counts": {},
        "queues": {},
        "coverage": {},
        "campaigns_named": [],
        "campaign_traces": {},
        "account_92": {},
        "account_79": {},
        "contact_92": {},
        "classification_notes": {},
    }
    try:
        # Global counts
        for table in (
            "campaigns",
            "contacts",
            "messages",
            "campaign_recipients",
            "accounts",
            "staged_queue_items",
            "campaign_accounts",
            "message_attempts",
            "channel_sessions",
        ):
            out["counts"][table] = db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()

        # Find campaigns by name patterns
        camps = (
            db.query(Campaign)
            .filter(
                (Campaign.name.ilike("%rubika%test%"))
                | (Campaign.title.ilike("%rubika%test%"))
                | (Campaign.name.ilike("%rubikaa%"))
                | (Campaign.title.ilike("%rubikaa%"))
            )
            .order_by(Campaign.id.asc())
            .all()
        )
        # Also list recent campaigns that might match
        recent = (
            db.query(Campaign)
            .order_by(Campaign.id.desc())
            .limit(20)
            .all()
        )
        out["recent_campaigns"] = [
            {
                "id": c.id,
                "name": c.name,
                "title": c.title,
                "status": c.status,
                "platform": str(c.platform),
                "created_at": _iso(c.created_at),
                "updated_at": _iso(c.updated_at),
            }
            for c in recent
        ]
        out["name_match_campaigns"] = [
            {
                "id": c.id,
                "name": c.name,
                "title": c.title,
                "status": c.status,
                "platform": str(c.platform),
                "created_at": _iso(c.created_at),
                "updated_at": _iso(c.updated_at),
                "template_text_len": len(c.template_text or ""),
                "use_gpt": c.use_gpt,
                "include_products": c.include_products,
            }
            for c in camps
        ]

        # Explicit search for exact-ish names
        all_camps = db.query(Campaign).order_by(Campaign.id.asc()).all()
        owner_candidates = []
        for c in all_camps:
            n = (c.name or "").strip().lower()
            t = (c.title or "").strip().lower()
            if n in {"rubika test1", "rubikaa test 2", "rubika test 1", "rubikaa test2"} or (
                "rubika" in n and "test" in n
            ) or ("rubikaa" in n):
                owner_candidates.append(c)
            elif "rubika" in t and "test" in t:
                owner_candidates.append(c)

        # Deduplicate
        seen = set()
        owner_list = []
        for c in owner_candidates:
            if c.id in seen:
                continue
            seen.add(c.id)
            owner_list.append(c)

        redis = get_redis_client()
        for aid in (12, 79, 92):
            qk = queue_key("rubika", aid)
            out["queues"][str(aid)] = {
                "key": qk,
                "llen": await redis.llen(qk),
            }
            out["coverage"][str(aid)] = {
                "key": worker_account_coverage_key("rubika", aid),
                "active": await has_active_worker_coverage(
                    redis, platform="rubika", account_id=aid
                ),
            }

        # Phase / schedules
        phase = resolve_current_phase(db)
        out["resolved_phase"] = phase
        out["active_schedules"] = [
            {
                "id": s.id,
                "phase": s.phase,
                "is_active": s.is_active,
                "start_hour": s.start_hour,
                "end_hour": s.end_hour,
                "max_per_hour": s.max_per_hour,
            }
            for s in db.query(RubikaSenderSchedule)
            .filter(RubikaSenderSchedule.is_active.is_(True))
            .all()
        ]

        # Trace each owner campaign (+ fallback: all rubika campaigns with status draft/prepared created recently)
        targets = owner_list
        if not targets:
            # fallback: campaigns created after 2026-08-29 07:50 UTC that are not r22/p6
            for c in all_camps:
                if c.created_at and c.created_at >= datetime(2026, 8, 29, 7, 50, 0):
                    n = (c.name or "")
                    if not n.startswith("r22-") and not n.startswith("p6-"):
                        targets.append(c)

        for c in targets:
            trace = await _trace_campaign(db, redis, c)
            out["campaign_traces"][str(c.id)] = trace

        # Account 92 and 79 deep
        for aid in (92, 79, 12):
            acct = db.query(Account).filter(Account.id == aid).first()
            if not acct:
                out[f"account_{aid}"] = {"exists": False}
                continue
            sessions = (
                db.query(ChannelSession)
                .filter(ChannelSession.account_id == aid)
                .order_by(ChannelSession.id.asc())
                .all()
            )
            pool = (
                db.query(RubikaAccountPool)
                .filter(RubikaAccountPool.account_id == aid)
                .all()
            )
            readiness = evaluate_account_session_readiness(db, acct)
            try:
                transport = await evaluate_rubika_send_preflight(
                    db, aid, consume_circuit_probe=False
                )
                transport_dict = transport.to_dict() if hasattr(transport, "to_dict") else {
                    "code": getattr(transport, "code", None),
                    "allowed": getattr(transport, "allowed", None),
                    "message": getattr(transport, "message", None),
                }
            except Exception as exc:
                transport_dict = {
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:500],
                }
            out[f"account_{aid}"] = {
                "exists": True,
                "id": acct.id,
                "platform": str(acct.platform),
                "status": str(acct.status),
                "label": acct.label,
                "masked_phone": _mask(acct.phone_number),
                "created_at": _iso(getattr(acct, "created_at", None)),
                "session_readiness": readiness if isinstance(readiness, dict) else str(readiness),
                "sessions": [
                    {
                        "id": s.id,
                        "session_type": str(s.session_type),
                        "created_at": _iso(s.created_at),
                        "updated_at": _iso(s.updated_at),
                        "cipher_len": len(s.ciphertext or ""),
                        "has_ciphertext": bool(s.ciphertext),
                        "key_version": s.key_version,
                    }
                    for s in sessions
                ],
                "pool_rows": [
                    {
                        "id": p.id,
                        "phase": p.phase,
                        "priority": p.priority,
                        "created_at": _iso(getattr(p, "created_at", None)),
                    }
                    for p in pool
                ],
                "transport_preflight": transport_dict,
            }

        # Contact 92 vs Account 92 distinction
        contact = db.query(Contact).filter(Contact.id == 92).first()
        account92 = db.query(Account).filter(Account.id == 92).first()
        out["contact_92"] = {
            "exists": bool(contact),
            "id": 92,
            "alias": contact.first_name if contact else None,
            "consent": contact.consent_status if contact else None,
            "campaign_id_column": contact.campaign_id if contact else None,
            "created_at": _iso(contact.created_at) if contact else None,
            "masked_phone": _mask(contact.phone_e164 or contact.phone) if contact else None,
            "note": "contacts.id is independent of accounts.id; same numeric id does not mean same entity",
        }
        out["entity_collision_92"] = {
            "contact_92_exists": bool(contact),
            "account_92_exists": bool(account92),
            "same_entity": False,
            "explanation": "Contact.id=92 and Account.id=92 are different tables/FKs; colliding integers are coincidence unless joined explicitly.",
            "account_92_label": account92.label if account92 else None,
            "account_92_masked": _mask(account92.phone_number) if account92 else None,
            "account_92_created_at": _iso(getattr(account92, "created_at", None)) if account92 else None,
        }

        # Contact 92 dependencies for KEEP
        if contact:
            recip = (
                db.query(CampaignRecipient)
                .filter(CampaignRecipient.contact_id == 92)
                .all()
            )
            msgs = db.query(Message).filter(Message.contact_id == 92).all()
            staged = (
                db.query(StagedQueueItem)
                .filter(StagedQueueItem.contact_id == 92)
                .all()
            )
            out["contact_92_dependencies"] = {
                "campaign_recipients": [
                    {
                        "id": r.id,
                        "campaign_id": r.campaign_id,
                        "send_status": str(r.send_status),
                        "created_at": _iso(r.created_at),
                    }
                    for r in recip
                ],
                "messages": [
                    {
                        "id": m.id,
                        "campaign_id": m.campaign_id,
                        "account_id": m.account_id,
                        "created_at": _iso(m.created_at),
                    }
                    for m in msgs
                ],
                "staged": [
                    {
                        "id": s.id,
                        "campaign_id": s.campaign_id,
                        "status": s.status,
                        "created_at": _iso(s.created_at),
                    }
                    for s in staged
                ],
            }

        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"WROTE {OUT}")
        print(json.dumps({
            "counts": out["counts"],
            "name_matches": out["name_match_campaigns"],
            "recent": out["recent_campaigns"][:10],
            "traced_ids": list(out["campaign_traces"].keys()),
            "queues": out["queues"],
            "coverage": out["coverage"],
            "entity_92": out["entity_collision_92"],
        }, ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()


async def _trace_campaign(db, redis, c: Campaign) -> dict:
    links = (
        db.query(CampaignAccount)
        .filter(CampaignAccount.campaign_id == c.id)
        .order_by(CampaignAccount.priority.asc(), CampaignAccount.id.asc())
        .all()
    )
    recipients = (
        db.query(CampaignRecipient, Contact)
        .join(Contact, CampaignRecipient.contact_id == Contact.id)
        .filter(CampaignRecipient.campaign_id == c.id)
        .order_by(CampaignRecipient.id.asc())
        .all()
    )
    messages = db.query(Message).filter(Message.campaign_id == c.id).all()
    staged = db.query(StagedQueueItem).filter(StagedQueueItem.campaign_id == c.id).all()
    attempts = []
    for m in messages:
        for a in db.query(MessageAttempt).filter(MessageAttempt.message_id == m.id).all():
            attempts.append(
                {
                    "id": a.id,
                    "message_id": a.message_id,
                    "status": str(a.status),
                    "platform_message_id": a.platform_message_id,
                    "error_code": a.error_code,
                    "error_message": (a.error_message or "")[:300] if a.error_message else None,
                    "created_at": _iso(a.created_at),
                }
            )

    sender_details = []
    for link in links:
        acct = db.query(Account).filter(Account.id == link.account_id).first()
        sender_details.append(
            {
                "campaign_account_id": link.id,
                "account_id": link.account_id,
                "priority": link.priority,
                "enabled": link.enabled,
                "account_label": acct.label if acct else None,
                "account_status": str(acct.status) if acct else None,
                "masked_phone": _mask(acct.phone_number) if acct else None,
                "platform": str(acct.platform) if acct else None,
            }
        )

    # Preflight
    preflight = None
    preflight_error = None
    try:
        pf = await evaluate_campaign_send_preflight(db, c.id)
        preflight = pf.to_dict() if hasattr(pf, "to_dict") else {
            "code": getattr(pf, "code", None),
            "allowed_to_start": getattr(pf, "allowed_to_start", None),
            "message": getattr(pf, "message", None),
            "ready_accounts": getattr(pf, "ready_accounts", None),
        }
    except Exception as exc:
        preflight_error = {"type": type(exc).__name__, "message": str(exc)[:500]}

    # Heuristic provenance
    name = c.name or ""
    provenance = {
        "name": name,
        "starts_with_r22": name.startswith("r22-"),
        "starts_with_p6": name.startswith("p6-"),
        "looks_owner_ui_name": ("test" in name.lower()) and not name.startswith("r22-") and not name.startswith("p6-"),
        "created_at": _iso(c.created_at),
    }

    return {
        "campaign": {
            "id": c.id,
            "name": c.name,
            "title": c.title,
            "status": c.status,
            "platform": str(c.platform),
            "channel": c.channel,
            "template_text_preview": (c.template_text or "")[:120],
            "template_text_len": len(c.template_text or ""),
            "use_gpt": c.use_gpt,
            "include_products": c.include_products,
            "created_at": _iso(c.created_at),
            "updated_at": _iso(c.updated_at),
            "schedule_start_at": _iso(c.schedule_start_at),
            "daily_limit": c.daily_limit,
            "max_contacts": c.max_contacts,
        },
        "provenance_heuristic": provenance,
        "senders": sender_details,
        "recipients": [
            {
                "recipient_id": r.id,
                "contact_id": contact.id,
                "alias": contact.first_name,
                "consent": contact.consent_status,
                "send_status": str(r.send_status),
                "render_status": str(r.render_status),
                "final_message_id": r.final_message_id,
                "failure_reason": r.failure_reason,
                "masked_phone": _mask(contact.phone_e164 or contact.phone),
                "created_at": _iso(r.created_at),
            }
            for r, contact in recipients
        ],
        "messages": [
            {
                "id": m.id,
                "account_id": m.account_id,
                "contact_id": m.contact_id,
                "dedupe_key": m.dedupe_key,
                "created_at": _iso(m.created_at),
                "has_rendered_text": bool(m.rendered_text),
            }
            for m in messages
        ],
        "staged": [
            {
                "id": s.id,
                "contact_id": s.contact_id,
                "status": s.status,
                "skip_reason": s.skip_reason,
                "rendered_message_id": s.rendered_message_id,
                "created_at": _iso(s.created_at),
            }
            for s in staged
        ],
        "attempts": attempts,
        "preflight": preflight,
        "preflight_error": preflight_error,
    }


if __name__ == "__main__":
    asyncio.run(main())
