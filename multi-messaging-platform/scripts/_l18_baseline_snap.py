from sqlalchemy import text
from core_engine.database import SessionLocal
db=SessionLocal()
try:
    print("MSG", db.execute(text("SELECT COUNT(*) FROM message_attempts")).scalar())
    print("CH", db.execute(text("SELECT COUNT(*) FROM rubika_login_challenges")).scalar())
    print("ACT", db.execute(text("SELECT COUNT(*) FROM channel_sessions WHERE session_status='active'")).scalar())
    rows=db.execute(text("SELECT account_id, id FROM channel_sessions WHERE session_status='active' ORDER BY account_id")).fetchall()
    print("ACTIVE_ROWS", [(int(r[0]), int(r[1])) for r in rows])
finally:
    db.close()
