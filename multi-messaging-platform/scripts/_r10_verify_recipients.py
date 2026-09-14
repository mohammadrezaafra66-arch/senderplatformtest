"""R10: verify approved contacts exist (safe aliases only; no raw phones)."""
from __future__ import annotations

import json

from core_engine.database import SessionLocal
from core_engine.models import Contact


def _mask(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 6:
        return "***"
    return f"{digits[:3]}***{digits[-3:]}"


def main() -> None:
    db = SessionLocal()
    try:
        out = []
        for cid in (2, 92):
            c = db.query(Contact).filter(Contact.id == cid).first()
            if c is None:
                out.append({"contact_id": cid, "exists": False})
                continue
            out.append(
                {
                    "contact_id": cid,
                    "exists": True,
                    "alias": c.first_name or f"contact-{cid}",
                    "consent": c.consent_status,
                    "masked_phone": _mask(c.phone_e164 or c.phone),
                    "has_e164": bool(c.phone_e164),
                }
            )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        ok = all(r.get("exists") and r.get("has_e164") and r.get("consent") == "allowed" for r in out)
        print(f"RECIPIENTS_OK={ok}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
