"""API داشبورد و آمار — Phase 5 Step 1."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core_engine.database import get_db
from core_engine.services.dashboard_service import (
    get_campaign_stats,
    get_dashboard_summary,
    get_workers_status,
)
from core_engine.services.redis_client import get_dashboard_queue_pending

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardHealthResponse(BaseModel):
    status: str
    service: str
    timestamp: str


class DashboardSummaryResponse(BaseModel):
    campaigns_total: int
    campaigns_running: int
    campaigns_paused: int
    messages_total: int
    messages_sent: int
    messages_failed: int
    accounts_total: int
    accounts_active: int
    accounts_banned: int


class CampaignStatsResponse(BaseModel):
    campaign_id: int
    total_recipients: int
    queued: int
    processing: int
    sent: int
    failed: int
    progress_percent: float
    eta_seconds: int | None = None


class WorkerStatusItem(BaseModel):
    name: str
    status: str
    last_seen_at: str | None = None


class WorkersStatusResponse(BaseModel):
    workers: list[WorkerStatusItem]


class QueueStatusItem(BaseModel):
    name: str
    pending: int


class QueuesStatusResponse(BaseModel):
    queues: list[QueueStatusItem] = Field(default_factory=list)


@router.get("/health", response_model=DashboardHealthResponse)
def dashboard_health():
    return DashboardHealthResponse(
        status="ok",
        service="dashboard",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/summary", response_model=DashboardSummaryResponse)
def dashboard_summary(db: Annotated[Session, Depends(get_db)]):
    return DashboardSummaryResponse(**get_dashboard_summary(db))


@router.get("/campaigns/{campaign_id}/stats", response_model=CampaignStatsResponse)
def dashboard_campaign_stats(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    return CampaignStatsResponse(**get_campaign_stats(db, campaign_id))


@router.get("/workers/status", response_model=WorkersStatusResponse)
def dashboard_workers_status():
    return WorkersStatusResponse(**get_workers_status())


@router.get("/queues/status", response_model=QueuesStatusResponse)
async def dashboard_queues_status():
    queues, _redis_connected = await get_dashboard_queue_pending()
    return QueuesStatusResponse(
        queues=[QueueStatusItem(**item) for item in queues],
    )
