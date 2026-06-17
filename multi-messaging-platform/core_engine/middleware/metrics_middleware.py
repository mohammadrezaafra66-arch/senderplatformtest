"""Middleware برای شمارش HTTP requests و ثبت duration."""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from core_engine.services.metrics_service import record_http_request


def _path_label(request: Request) -> str:
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        return str(route.path)
    return request.url.path


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start
        record_http_request(
            method=request.method,
            path=_path_label(request),
            status_code=response.status_code,
            duration_seconds=duration,
        )
        return response
