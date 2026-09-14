#!/usr/bin/env python3
from sqlalchemy import text
from core_engine.database import SessionLocal

db = SessionLocal()
try:
    rows = db.execute(
        text(
            "SELECT id, session_status::text FROM channel_sessions "
            "WHERE account_id=2 ORDER BY id"
        )
    ).fetchall()
    act = db.execute(
        text(
            "SELECT account_id, id FROM channel_sessions "
            "WHERE session_status='active' ORDER BY account_id"
        )
    ).fetchall()
    print("A2", [(int(a), b) for a, b in rows])
    print("ACTIVES", [(int(a), int(b)) for a, b in act])
finally:
    db.rollback()
    db.close()
