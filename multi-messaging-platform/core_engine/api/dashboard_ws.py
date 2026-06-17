"""WebSocket داشبورد — ارسال snapshot دوره‌ای وضعیت سیستم."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core_engine.services.dashboard_service import build_dashboard_snapshot
from core_engine.services.metrics_service import (
    ws_connection_closed,
    ws_connection_opened,
    ws_message_sent,
)

router = APIRouter(tags=["dashboard-ws"])

SNAPSHOT_INTERVAL_SECONDS = 3


@router.websocket("/dashboard/ws")
async def dashboard_websocket(websocket: WebSocket):
    await websocket.accept()
    ws_connection_opened()
    try:
        while True:
            snapshot = await build_dashboard_snapshot()
            await websocket.send_json(snapshot)
            ws_message_sent()
            await asyncio.sleep(SNAPSHOT_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        raise
    except Exception:
        # Client likely disconnected between send and sleep; exit quietly.
        return
    finally:
        ws_connection_closed()
