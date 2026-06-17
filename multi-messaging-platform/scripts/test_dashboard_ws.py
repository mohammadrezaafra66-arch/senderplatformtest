#!/usr/bin/env python3
"""Manual test for dashboard WebSocket — prints first 3 snapshots then disconnects."""

from __future__ import annotations

import asyncio
import json
import os
import sys

try:
    import websockets
except ImportError:
    print("Missing dependency: websockets. Install with: pip install websockets", file=sys.stderr)
    raise SystemExit(1) from None

WS_URL = os.getenv("DASHBOARD_WS_URL", "ws://localhost:8001/dashboard/ws")
MESSAGE_COUNT = 3


async def main() -> int:
    print(f"Connecting to {WS_URL}")
    async with websockets.connect(WS_URL) as websocket:
        for index in range(1, MESSAGE_COUNT + 1):
            raw = await websocket.recv()
            payload = json.loads(raw)
            print(f"\n--- message {index} ---")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            if payload.get("type") != "dashboard_snapshot":
                print(f"Unexpected message type: {payload.get('type')!r}", file=sys.stderr)
                return 1
    print("\nDisconnected cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
