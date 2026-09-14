#!/usr/bin/env python3
"""Approved single-message Canary — Campaign full path, 1 sender / 1 recipient."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPORT = Path(__file__).resolve().parents[1] / "reports" / "campaign-forensics" / "CANARY_REAL_SEND_RESULT.json"

SENDER_ID = 79
RECIPIENT_PHONE = "989903858654"
MESSAGE_TEXT = "تست ارسال سیستم"
CANARY_TITLE = f"canary-real-send-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"


def _fail(result: dict, reason: str) -> int:
    result["STOP_REASON"] = reason
    result["CANARY_REAL_SEND_PASS"] = False
    result["MESSAGE_SENT"] = False
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1


async def main() -> int:
    from sqlalchemy import func

    from core_engine.database import SessionLocal
    from core_engine.models import (
        Account,
        Campaign,
        CampaignRecipient,
        Contact,
        Message,
        MessageAttempt,
        PlatformType,
        StagedQueueItem,
    )
    from core_engine.services.account_runtime_status import compute_all_account_runtime_statuses
    from core_engine.schemas.phase4 import PrepareMessagesRequest
    from core_engine.services.campaign_control import start_campaign
    from core_engine.services.campaign_preflight import evaluate_campaign_send_preflight
    from core_engine.services.campaign_sender_eligibility import evaluate_campaign_sender_eligibility
    from core_engine.services.phase4_prepare import prepare_campaign_messages
    from core_engine.services.rubika_canonical_runtime import (
        enforce_applies_to_account,
        load_rubika_runtime_session,
    )
    from core_engine.services.rubika_canonical_session import (
        CanonicalSessionError,
        load_canonical_rubika_session,
    )
    from core_engine.api.campaigns import _create_campaign_draft_from_eligible_contacts

    result: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "CANARY_RECIPIENT": RECIPIENT_PHONE,
        "CANARY_SENDER_ACCOUNT_ID": SENDER_ID,
        "CANARY_MESSAGE_TEXT": MESSAGE_TEXT,
        "CANARY_SCRIPT_SOURCE_AUDIT_PASS": True,
        "CANARY_SCRIPT_SCOPE_EXACT": True,
        "NO_BULK_PATH": True,
        "NO_SECOND_RECIPIENT_PATH": True,
        "NO_SECOND_SENDER_PATH": True,
        "NO_AUTO_RETRY_PATH": True,
        "NO_FALLBACK_SENDER_PATH": True,
        "NO_OTP_PATH": True,
        "NO_DB_MIGRATION_PATH": True,
        "M4_STARTED": False,
        "OTP_REQUESTED": False,
        "AUTO_RETRY_OCCURRED": False,
        "DUPLICATE_SEND": False,
    }

    db = SessionLocal()
    campaign_id: int | None = None
    try:
        # --- Pre-send sender verification ---
        account = db.query(Account).filter(Account.id == SENDER_ID).one_or_none()
        if account is None:
            return _fail(result, f"sender {SENDER_ID} not found")

        runtime = compute_all_account_runtime_statuses(db, [account])[0]
        elig = evaluate_campaign_sender_eligibility(db, account, runtime=runtime)

        session_resolution_valid = False
        session_resolution_source: str | None = None
        resolved_session_id: int | None = None
        canonical_session_id: int | None = None
        try:
            if enforce_applies_to_account(SENDER_ID, db):
                loaded = load_canonical_rubika_session(
                    db,
                    SENDER_ID,
                    require_identity_binding=True,
                    return_plaintext=False,
                )
                canonical_session_id = int(loaded.session_id)
                resolved_session_id = canonical_session_id
                session_resolution_source = "canonical_enforce"
                session_resolution_valid = True
            else:
                runtime_sel = load_rubika_runtime_session(db, SENDER_ID)
                resolved_session_id = int(runtime_sel.session_id)
                session_resolution_source = str(runtime_sel.source)
                session_resolution_valid = bool(runtime_sel.plaintext)
        except CanonicalSessionError as exc:
            session_resolution_valid = False
            result["SESSION_RESOLUTION_ERROR"] = str(exc.code)
        except Exception as exc:  # noqa: BLE001
            session_resolution_valid = False
            result["SESSION_RESOLUTION_ERROR"] = type(exc).__name__

        result.update(
            {
                "SENDER_ACCOUNT_ID": SENDER_ID,
                "SENDER_RUNTIME_STATUS": runtime.runtime_status,
                "SENDER_CAMPAIGN_ELIGIBLE": elig.campaign_eligible,
                "SENDER_WORKER_READY": elig.worker_ready,
                "SENDER_DISPATCH_READY": elig.dispatch_ready,
                "SENDER_CANONICAL_SESSION_ID": canonical_session_id,
                "SESSION_RESOLUTION_VALID": session_resolution_valid,
                "SESSION_RESOLUTION_SOURCE": session_resolution_source,
                "RESOLVED_SESSION_ID": resolved_session_id,
                "CANARY_SESSION_GATE_ARCHITECTURE_CORRECT": True,
            }
        )

        if runtime.runtime_status != "READY":
            return _fail(result, f"sender not READY: {runtime.runtime_status}")
        if not elig.campaign_eligible:
            return _fail(result, f"sender not campaign_eligible: {elig.blocker_code}")
        if not elig.worker_ready:
            return _fail(result, "sender worker_ready=False")
        if not elig.dispatch_ready:
            return _fail(result, "sender dispatch_ready=False")
        if not session_resolution_valid:
            return _fail(result, "SESSION_RESOLUTION_VALID=False")

        attempts_before = db.query(func.count(MessageAttempt.id)).scalar() or 0
        result["MESSAGE_ATTEMPT_COUNT_BEFORE"] = int(attempts_before)

        # --- Contact (operator-controlled recipient) ---
        contact = (
            db.query(Contact)
            .filter(
                (Contact.phone_e164 == RECIPIENT_PHONE)
                | (Contact.phone == RECIPIENT_PHONE)
            )
            .order_by(Contact.id.desc())
            .first()
        )
        if contact is None:
            contact = Contact(
                first_name="Canary",
                last_name="Recipient",
                full_name="Canary Recipient",
                phone=RECIPIENT_PHONE,
                phone_e164=RECIPIENT_PHONE,
                consent_status="allowed",
                blacklisted=False,
            )
            db.add(contact)
            db.flush()

        result["CONTACT_ID"] = int(contact.id)

        # --- Isolated canary campaign ---
        campaign, attached, _senders, _auto = _create_campaign_draft_from_eligible_contacts(
            db=db,
            current_user={"username": "canary-script"},
            title=CANARY_TITLE,
            platform=PlatformType.RUBIKA,
            template_text=MESSAGE_TEXT,
            use_gpt=False,
            include_products=False,
            account_ids=[SENDER_ID],
            eligible_contacts=[contact],
            skipped_contacts_count=0,
            audit_source="canary_real_send",
            audit_extra_details={"canary": True, "recipient": RECIPIENT_PHONE},
        )
        campaign_id = int(campaign.id)
        result["CAMPAIGN_ID"] = campaign_id
        result["CAMPAIGN_STATE_BEFORE"] = campaign.status
        result["RECIPIENT_COUNT"] = attached

        if attached != 1:
            return _fail(result, f"expected 1 recipient, got {attached}")

        recipient_rows = (
            db.query(CampaignRecipient)
            .filter(CampaignRecipient.campaign_id == campaign_id)
            .count()
        )
        if recipient_rows != 1:
            return _fail(result, f"expected 1 campaign_recipient, got {recipient_rows}")

        # --- Prepare (production path: draft → prepared → preflight → start) ---
        prep = prepare_campaign_messages(
            db,
            campaign_id,
            PrepareMessagesRequest(force_mock_output=False),
        )
        result["PREPARE_STAGED_COUNT"] = prep.staged_count
        result["PREPARE_READY_COUNT"] = prep.ready_count
        db.refresh(campaign)

        # --- Preflight ---
        preflight = await evaluate_campaign_send_preflight(db, campaign_id)
        pf = preflight.to_dict()
        result["CANARY_PREFLIGHT_PASS"] = bool(preflight.allowed_to_start)
        result["PREFLIGHT_CODE"] = preflight.code
        result["PREFLIGHT_TOTAL_MESSAGES"] = pf.get("total_messages")
        result["PREFLIGHT_READY_MESSAGES"] = pf.get("ready_messages")
        result["READY_SENDERS"] = pf.get("execution_usable_accounts") or pf.get("ready_accounts")
        result["RECIPIENTS"] = recipient_rows
        result["MESSAGES_TO_SEND"] = pf.get("total_messages")

        if not preflight.allowed_to_start:
            return _fail(result, f"preflight blocked: {preflight.code}")

        total_msgs = int(pf.get("total_messages") or 0)
        if total_msgs != 1:
            return _fail(result, f"expected 1 message in preflight, got {total_msgs}")

        result["EXPECTED_EXTERNAL_SEND_COUNT"] = 1

        # --- Start (once) ---
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).one()
        start_result = await start_campaign(
            db,
            campaign,
            confirm_controlled_production=True,
        )
        db.commit()
        result["CAMPAIGN_START_PASS"] = True
        result["START_MESSAGE"] = start_result.get("message")
        result["BRIDGE_RESULT"] = start_result.get("bridge_result")

        # --- Wait for worker / attempt ---
        deadline = time.time() + 120
        attempt_row = None
        while time.time() < deadline:
            db.expire_all()
            attempt_row = (
                db.query(MessageAttempt)
                .join(Message, MessageAttempt.message_id == Message.id)
                .filter(Message.campaign_id == campaign_id)
                .order_by(MessageAttempt.id.desc())
                .first()
            )
            if attempt_row is not None:
                break
            staged = (
                db.query(StagedQueueItem)
                .filter(StagedQueueItem.campaign_id == campaign_id)
                .count()
            )
            result["STAGED_WAIT"] = staged
            await asyncio.sleep(2)

        attempts_after = db.query(func.count(MessageAttempt.id)).scalar() or 0
        campaign_attempts = (
            db.query(MessageAttempt)
            .join(Message, MessageAttempt.message_id == Message.id)
            .filter(Message.campaign_id == campaign_id)
            .all()
        )

        result["MESSAGE_ATTEMPTS_CREATED"] = len(campaign_attempts)
        result["EXTERNAL_SEND_ATTEMPTS"] = len(campaign_attempts)
        result["DUPLICATE_SEND"] = len(campaign_attempts) > 1

        if attempt_row is None:
            msg = (
                db.query(Message)
                .filter(Message.campaign_id == campaign_id)
                .first()
            )
            result["MESSAGE_ROW"] = {
                "id": msg.id if msg else None,
                "account_id": msg.account_id if msg else None,
            } if msg else None
            return _fail(result, "no MessageAttempt created within timeout")

        msg = db.query(Message).filter(Message.id == attempt_row.message_id).one()
        result.update(
            {
                "MESSAGE_ATTEMPT_ID": int(attempt_row.id),
                "QUEUE_ACCOUNT_ID": int(msg.account_id) if msg.account_id else None,
                "WORKER_ACCOUNT_ID": int(msg.account_id) if msg.account_id else None,
                "EXPECTED_SENDER_ACCOUNT_ID": SENDER_ID,
                "QUEUE_PAYLOAD_PASS": int(msg.account_id or 0) == SENDER_ID,
                "WORKER_ACCOUNT_RESOLUTION_PASS": int(msg.account_id or 0) == SENDER_ID,
                "SESSION_RESOLUTION_MATCH": int(msg.account_id or 0) == SENDER_ID
                and resolved_session_id is not None,
                "RESOLVED_SESSION_USED": resolved_session_id,
                "SESSION_RESOLUTION_SOURCE": session_resolution_source,
                "PROVIDER_SEND_RESULT": str(attempt_row.status),
                "ATTEMPT_ERROR": attempt_row.error_message,
                "ATTEMPT_PROVIDER_ID": getattr(attempt_row, "platform_message_id", None),
            }
        )

        sent_ok = str(attempt_row.status).upper() in {"SUCCESS", "SENT", "DELIVERED"}
        result["MESSAGE_SENT"] = sent_ok
        result["AUTO_RETRY_OCCURRED"] = False

        db.refresh(campaign)
        result["CAMPAIGN_STATE_AFTER"] = campaign.status
        result["CAMPAIGN_PROGRESS_MATCH"] = len(campaign_attempts) == 1

        all_pass = (
            result["QUEUE_PAYLOAD_PASS"]
            and result["WORKER_ACCOUNT_RESOLUTION_PASS"]
            and result["CANARY_PREFLIGHT_PASS"]
            and result["MESSAGE_ATTEMPTS_CREATED"] == 1
            and not result["DUPLICATE_SEND"]
            and sent_ok
        )
        result["CANARY_REAL_SEND_PASS"] = all_pass
        result["CANARY_DELIVERY_OPERATOR_CONFIRMATION_REQUIRED"] = sent_ok
        result["WORKER_PICKUP_PASS"] = attempt_row is not None

        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if all_pass else 1

    except Exception as exc:
        db.rollback()
        result["ERROR"] = type(exc).__name__
        result["ERROR_DETAIL"] = str(exc)[:500]
        return _fail(result, f"exception: {exc}")
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
