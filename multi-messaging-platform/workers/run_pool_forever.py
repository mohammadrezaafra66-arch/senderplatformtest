"""Long-running multi-account worker pool entrypoint."""

from __future__ import annotations

import asyncio
import sys

from workers.pool_factory import build_pool_worker


async def main() -> int:
    worker = build_pool_worker()
    await worker.run_forever()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
