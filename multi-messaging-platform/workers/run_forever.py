"""Long-running worker process entrypoint."""

from __future__ import annotations

import asyncio
import sys

from workers.factory import build_worker


async def main() -> int:
    worker = build_worker()
    await worker.run_forever()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
