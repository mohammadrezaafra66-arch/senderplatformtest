"""Prometheus metrics endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from core_engine.services.metrics_service import get_metrics_output, refresh_dynamic_metrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=True)
async def prometheus_metrics() -> Response:
    await refresh_dynamic_metrics()
    content, content_type = get_metrics_output()
    return Response(content=content, media_type=content_type)
