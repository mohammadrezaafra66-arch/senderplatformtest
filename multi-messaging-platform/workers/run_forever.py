"""Long-running worker process entrypoint."""

from __future__ import annotations

import asyncio
import sys

from workers.base_worker import WorkerExecutionDisabled
from workers.config import get_worker_settings
from workers.factory import WorkerIdentityConflict, build_worker
from workers.pool_factory import build_pool_worker


async def main() -> int:
    cfg = get_worker_settings()
    platform = cfg.WORKER_PLATFORM.lower().strip()

    # Rubika multi-account pool when explicitly enabled (R4).
    if platform == "rubika" and bool(getattr(cfg, "RUBIKA_MULTI_ACCOUNT_WORKER", False)):
        worker = build_pool_worker(cfg)
    else:
        worker = build_worker(cfg)

    await worker.run_forever()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except WorkerExecutionDisabled as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    except WorkerIdentityConflict as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(3) from exc
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
