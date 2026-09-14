from sqlalchemy import text
from core_engine.database import SessionLocal

db = SessionLocal()
try:
    rows = db.execute(
        text(
            """
            SELECT id, username, action, resource_type, resource_id, timestamp, details
            FROM audit_logs
            WHERE resource_type = 'campaign' AND resource_id IN ('1','2','3','4')
            ORDER BY id
            """
        )
    ).fetchall()
    for r in rows:
        print(dict(r._mapping))
finally:
    db.close()
