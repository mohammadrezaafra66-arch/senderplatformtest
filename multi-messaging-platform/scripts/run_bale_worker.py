"""Entry point for BaleWorker."""
import asyncio
import os
import sys

sys.path.insert(0, "/app")

from workers.bale_worker import BaleWorker

async def main():
    account_id = int(os.getenv("WORKER_ACCOUNT_ID", "1"))
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    database_url = os.getenv("DATABASE_URL", "")
    poll_interval = int(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "5"))

    worker = BaleWorker(
        account_id=account_id,
        redis_url=redis_url,
        database_url=database_url,
        poll_interval_seconds=poll_interval,
        log_level="INFO",
    )
    print(f"✅ BaleWorker started for account_id={account_id}")
    await worker.run_forever()

if __name__ == "__main__":
    asyncio.run(main())
