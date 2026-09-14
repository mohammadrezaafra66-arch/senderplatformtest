"""READ-ONLY: Account92 decrypt exception class + audit provenance for campaigns 1/2."""
from __future__ import annotations

import json
import traceback
from pathlib import Path

from sqlalchemy import text

from core_engine.database import SessionLocal
from core_engine.models import Account, ChannelSession
from core_engine.services.account_session_wiring import (
    _latest_session_row,
    evaluate_account_session_readiness,
)
from core_engine.services.crypto import SessionDecryptionError
from core_engine.services.session_storage import load_channel_session_plaintext
from core_engine.models import SessionType

OUT = Path("/tmp/r10_forensic_out/OWNER_DECRYPT_AUDIT.json")


def main() -> None:
    db = SessionLocal()
    out: dict = {}
    try:
        # Schema discovery
        for table in ("audit_logs", "audit_events"):
            cols = db.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :t ORDER BY ordinal_position"
                ),
                {"t": table},
            ).fetchall()
            out[f"schema_{table}"] = [c[0] for c in cols]

        # Audit rows mentioning campaigns 1/2 if schema allows
        audits = []
        cols = out.get("schema_audit_logs") or []
        if cols:
            # build safe select of common columns
            select_cols = [c for c in cols if c in {
                "id", "action", "event", "entity_type", "entity_id", "resource_type",
                "resource_id", "created_at", "details", "payload", "message", "actor",
                "user_id", "source",
            }]
            if select_cols:
                q = f"SELECT {', '.join(select_cols)} FROM audit_logs ORDER BY id DESC LIMIT 50"
                try:
                    rows = db.execute(text(q)).fetchall()
                    for r in rows:
                        d = dict(r._mapping)
                        blob = json.dumps(d, default=str)
                        if any(tok in blob for tok in ('"entity_id\": 1', '"entity_id\": 2', "rubika test", "campaign_id\": 1", "campaign_id\": 2", "create_campaign")):
                            audits.append({k: (str(v)[:500] if v is not None else None) for k, v in d.items()})
                    out["audit_logs_matched_sample"] = audits[:20]
                    out["audit_logs_scanned"] = len(rows)
                except Exception as exc:
                    db.rollback()
                    out["audit_logs_error"] = f"{type(exc).__name__}: {exc}"

        # Campaign 1/2 in R0 extract?
        r0_camps = Path("/tmp/r0_extract/r0_campaigns.sql")
        if r0_camps.exists():
            text_data = r0_camps.read_text(encoding="utf-8", errors="replace")
            out["r0_campaigns_sql_contains_rubika_test1"] = "rubika test1" in text_data
            out["r0_campaigns_sql_contains_rubikaa_test_2"] = "rubikaa test 2" in text_data
            # extract first two campaign id lines if COPY
            rows = []
            in_copy = False
            for line in text_data.splitlines():
                if line.startswith("COPY "):
                    in_copy = True
                    continue
                if in_copy and line.startswith("\\."):
                    break
                if in_copy and line:
                    rows.append(line.split("\t")[:5])
            out["r0_campaign_rows_preview"] = rows[:6]

        # Decrypt probe for account 92 — capture exception type only
        acct = db.query(Account).filter(Account.id == 92).first()
        readiness = evaluate_account_session_readiness(db, acct)
        out["account_92_readiness"] = {
            "ready": readiness.ready,
            "code": readiness.code,
            "message": readiness.message,
            "error": readiness.error,
            "session_type": str(readiness.session_type),
        }

        row = _latest_session_row(db, 92, SessionType.RUBIKA_SESSION)
        out["account_92_latest_session"] = {
            "id": row.id if row else None,
            "key_version": row.key_version if row else None,
            "cipher_len": len(row.ciphertext or "") if row else None,
            "created_at": row.created_at.isoformat() if row and row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
            "ciphertext_looks_fernet": bool(row and row.ciphertext and row.ciphertext.startswith("gAAAA")),
        }

        decrypt_result = {"ok": False}
        if row is not None:
            try:
                plaintext = load_channel_session_plaintext(row)
                decrypt_result = {
                    "ok": True,
                    "plaintext_len": len(plaintext),
                    # do NOT include plaintext
                }
            except SessionDecryptionError as exc:
                decrypt_result = {
                    "ok": False,
                    "exception_type": "SessionDecryptionError",
                    "exception_message": str(exc)[:300],
                    "raised_from_module": type(exc).__module__,
                }
            except Exception as exc:
                decrypt_result = {
                    "ok": False,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:300],
                    "traceback_tail": traceback.format_exc()[-800:],
                }
        out["account_92_decrypt_probe"] = decrypt_result

        # Also probe both session rows independently
        per_row = []
        for s in (
            db.query(ChannelSession)
            .filter(ChannelSession.account_id == 92)
            .order_by(ChannelSession.id.asc())
            .all()
        ):
            entry = {
                "session_id": s.id,
                "key_version": s.key_version,
                "cipher_len": len(s.ciphertext or ""),
                "fernet_prefix": bool(s.ciphertext and s.ciphertext.startswith("gAAAA")),
            }
            try:
                load_channel_session_plaintext(s)
                entry["decrypt"] = "OK"
            except SessionDecryptionError as exc:
                entry["decrypt"] = "SessionDecryptionError"
                entry["message"] = str(exc)[:300]
            except Exception as exc:
                entry["decrypt"] = type(exc).__name__
                entry["message"] = str(exc)[:300]
            per_row.append(entry)
        out["account_92_per_session_decrypt"] = per_row

        # Control: account 79 should decrypt OK
        acct79 = db.query(Account).filter(Account.id == 79).first()
        r79 = evaluate_account_session_readiness(db, acct79)
        out["account_79_readiness"] = {
            "ready": r79.ready,
            "code": r79.code,
            "message": r79.message,
        }
        row79 = _latest_session_row(db, 79, SessionType.RUBIKA_SESSION)
        try:
            load_channel_session_plaintext(row79)
            out["account_79_decrypt"] = "OK"
        except Exception as exc:
            out["account_79_decrypt"] = f"{type(exc).__name__}: {str(exc)[:200]}"

        # key_version distribution across all rubika sessions (no secrets)
        kv = db.execute(
            text(
                """
                SELECT key_version, count(*)
                FROM channel_sessions
                WHERE session_type::text ILIKE '%rubika%'
                GROUP BY key_version
                ORDER BY key_version
                """
            )
        ).fetchall()
        out["rubika_session_key_version_counts"] = [
            {"key_version": r[0], "count": r[1]} for r in kv
        ]

        # Compare SESSION_SECRET configured presence only (not value)
        from core_engine.config import get_settings
        settings = get_settings()
        secret = getattr(settings, "SESSION_SECRET", None) or ""
        out["session_secret_present"] = bool(secret)
        out["session_secret_len"] = len(secret)
        # Fernet key derivation fingerprint without revealing secret
        import hashlib
        out["session_secret_sha16"] = hashlib.sha256(secret.encode()).hexdigest()[:16] if secret else None

        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    finally:
        db.close()


if __name__ == "__main__":
    main()
