"""واکشی قیمت محصولات از API داخلی و کش در Redis."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from core_engine.config import get_settings
from core_engine.services.pricing_playwright_scraper import (
    scrape_all_pricing_pages_with_playwright,
)
from core_engine.services.pricing_scraper import scrape_pricing_page
from core_engine.services.redis_client import get_redis_client

PRICING_CACHE_KEY = "cache:products:pricing"
PRICING_API_TIMEOUT_SECONDS = 10
LIST_KEYS = ("data", "items", "products", "results")
BODY_PREVIEW_MAX_LEN = 500
CANDIDATE_BODY_PREVIEW_MAX_LEN = 300

DEFAULT_AMIN_HOZOOR_PRICING_URL = (
    "http://192.168.170.10:3000/pricing/amin-hozoor-board"
)
AMIN_HOZOOR_PRICING_CANDIDATES = (
    "http://192.168.170.10:3000/pricing/amin-hozoor-board",
    "http://192.168.170.10:3000/api/pricing/amin-hozoor-board",
    "http://192.168.170.10:3000/api/pricing/amin-hozoor-board/",
    "http://192.168.170.10:3000/pricing/api/amin-hozoor-board",
    "http://192.168.170.10:3000/pricing/amin-hozoor-board/api",
    "http://192.168.170.10:3000/api/amin-hozoor-board",
    "http://192.168.170.10:3000/api/prices",
    "http://192.168.170.10:3000/api/products",
    "http://192.168.170.10:3000/api/pricing",
)


class PricingApiError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        content_type: str | None = None,
        body_preview: str | None = None,
        final_url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.content_type = content_type
        self.body_preview = body_preview
        self.final_url = final_url

    def to_error_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "success": False,
            "error": str(self),
        }
        if self.status_code is not None:
            result["status_code"] = self.status_code
        if self.content_type is not None:
            result["content_type"] = self.content_type
        if self.body_preview is not None:
            result["body_preview"] = self.body_preview
        if self.final_url is not None:
            result["final_url"] = self.final_url
        return result


def _body_preview(text: str, max_len: int = BODY_PREVIEW_MAX_LEN) -> str:
    return text[:max_len]


def _response_diagnostics(response: httpx.Response) -> dict[str, Any]:
    return {
        "status_code": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "body_preview": _body_preview(response.text),
        "final_url": str(response.url),
    }


def _is_json_like(content_type: str | None, text: str) -> bool:
    if content_type and "application/json" in content_type.lower():
        return True
    stripped = text.strip()
    return stripped.startswith("{") or stripped.startswith("[")


def _candidate_body_preview(text: str) -> str:
    return text[:CANDIDATE_BODY_PREVIEW_MAX_LEN]


def _origin_from_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _build_base_pricing_candidates(pricing_api_url: str) -> list[str]:
    normalized = pricing_api_url.rstrip("/")
    default_normalized = DEFAULT_AMIN_HOZOOR_PRICING_URL.rstrip("/")
    if normalized == default_normalized:
        return list(AMIN_HOZOOR_PRICING_CANDIDATES)
    return [pricing_api_url]


def _extract_api_paths_from_html(html: str, base_url: str) -> list[str]:
    origin = _origin_from_url(base_url)
    discovered: set[str] = set()

    for match in re.finditer(r'["\'](/api/[^"\'?\s<>]+)["\']', html):
        path = match.group(1).rstrip("/")
        if path and len(path) <= 200:
            discovered.add(f"{origin}{path}")

    if "__NEXT_DATA__" in html:
        next_data_match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if next_data_match:
            next_data_content = next_data_match.group(1)
            for match in re.finditer(r'"/api/[^"\\]+"', next_data_content):
                path = match.group(0).strip('"').rstrip("/")
                if path and len(path) <= 200:
                    discovered.add(f"{origin}{path}")

    return sorted(discovered)


def _try_json_parse_success(status_code: int, text: str) -> bool:
    if status_code != 200:
        return False
    try:
        json.loads(text)
        return True
    except json.JSONDecodeError:
        return False


async def _probe_candidate_url(
    client: httpx.AsyncClient,
    url: str,
) -> dict[str, Any]:
    try:
        response = await client.get(url)
    except httpx.RequestError as exc:
        return {
            "url": url,
            "error": f"Request failed: {exc}",
        }

    content_type = response.headers.get("content-type", "")
    body_text = response.text
    return {
        "url": url,
        "status_code": response.status_code,
        "content_type": content_type,
        "is_json_like": _is_json_like(content_type, body_text),
        "json_parse_success": _try_json_parse_success(
            response.status_code,
            body_text,
        ),
        "body_preview": _candidate_body_preview(body_text),
    }


async def probe_pricing_candidates() -> dict[str, Any]:
    settings = get_settings()
    pricing_api_url = settings.PRICING_API_URL

    candidates: list[str] = []
    seen: set[str] = set()

    def _add_candidate(url: str) -> None:
        normalized = url.rstrip("/")
        if normalized not in seen:
            seen.add(normalized)
            candidates.append(url)

    for url in _build_base_pricing_candidates(pricing_api_url):
        _add_candidate(url)

    async with httpx.AsyncClient(timeout=PRICING_API_TIMEOUT_SECONDS) as client:
        try:
            html_response = await client.get(pricing_api_url)
            content_type = html_response.headers.get("content-type", "")
            if "text/html" in content_type.lower():
                for discovered_url in _extract_api_paths_from_html(
                    html_response.text,
                    pricing_api_url,
                ):
                    _add_candidate(discovered_url)
        except httpx.RequestError:
            pass

        results = [
            await _probe_candidate_url(client, url)
            for url in candidates
        ]

    output: dict[str, Any] = {
        "configured_pricing_api_url": pricing_api_url,
        "candidates_tested": len(results),
        "results": results,
    }

    for result in results:
        if result.get("json_parse_success"):
            output["recommended_pricing_api_url"] = result["url"]
            output["reason"] = "This candidate returned valid JSON"
            return output

    output["message"] = (
        "No valid JSON pricing endpoint found. "
        "The pricing app currently exposes an HTML page, not a JSON API."
    )
    return output


async def probe_pricing_api() -> dict[str, Any]:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=PRICING_API_TIMEOUT_SECONDS) as client:
            response = await client.get(settings.PRICING_API_URL)
    except httpx.RequestError as exc:
        return {
            "success": False,
            "error": f"Pricing API request failed: {exc}",
        }

    content_type = response.headers.get("content-type", "")
    return {
        "success": True,
        "status_code": response.status_code,
        "content_type": content_type,
        "body_preview": _body_preview(response.text),
        "final_url": str(response.url),
        "is_json_like": _is_json_like(content_type, response.text),
    }


async def fetch_pricing_from_api() -> dict[str, Any] | list[Any]:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=PRICING_API_TIMEOUT_SECONDS) as client:
            response = await client.get(settings.PRICING_API_URL)
    except httpx.RequestError as exc:
        raise ValueError(f"Pricing API request failed: {exc}") from exc

    diagnostics = _response_diagnostics(response)

    if response.status_code != 200:
        raise ValueError(
            f"Pricing API request failed: HTTP {response.status_code}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise PricingApiError(
            "Pricing API returned invalid JSON",
            **diagnostics,
        ) from exc


def normalize_pricing_payload(raw_payload: Any) -> dict[str, Any]:
    normalized_items: list[Any] = []

    if isinstance(raw_payload, list):
        normalized_items = raw_payload
    elif isinstance(raw_payload, dict):
        for key in LIST_KEYS:
            value = raw_payload.get(key)
            if isinstance(value, list):
                normalized_items = value
                break

    return {
        "source": "pricing_api",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "raw_payload": raw_payload,
        "normalized_items": normalized_items,
    }


def normalize_scraper_payload(scraper_result: dict[str, Any]) -> dict[str, Any]:
    source = str(scraper_result.get("source") or "html_scraper")
    return {
        "source": source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "raw_payload": scraper_result,
        "normalized_items": scraper_result.get("items") or [],
        "warnings": scraper_result.get("warnings") or [],
    }


async def _write_pricing_cache(normalized: dict[str, Any]) -> None:
    """Redis write for pricing cache only — not a worker message queue."""
    settings = get_settings()
    redis_client = get_redis_client()
    await redis_client.set(
        PRICING_CACHE_KEY,
        json.dumps(normalized, ensure_ascii=False),
        ex=settings.PRICING_CACHE_TTL_SECONDS,
    )


def _scraper_item_count(scraper_result: dict[str, Any] | None) -> int:
    if not scraper_result:
        return 0
    return int(scraper_result.get("item_count") or 0)


def _build_refresh_failure(
    *,
    error: str,
    source: str | None = None,
    warnings: list[str] | None = None,
    json_attempt: dict[str, Any] | None = None,
    static_attempt: dict[str, Any] | None = None,
    playwright_attempt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": False,
        "error": error,
    }
    if source:
        result["source"] = source
    if warnings:
        result["warnings"] = warnings
    if json_attempt:
        result["json_attempt"] = json_attempt
    if static_attempt:
        result["static_attempt"] = static_attempt
    if playwright_attempt:
        result["playwright_attempt"] = playwright_attempt
    return result


async def refresh_pricing_cache() -> dict[str, Any]:
    settings = get_settings()

    json_error: dict[str, Any] | None = None
    try:
        raw_payload = await fetch_pricing_from_api()
        normalized = normalize_pricing_payload(raw_payload)
        await _write_pricing_cache(normalized)
        return {
            "success": True,
            "source": "pricing_api",
            "cache_key": PRICING_CACHE_KEY,
            "ttl_seconds": settings.PRICING_CACHE_TTL_SECONDS,
            "fetched_at": normalized["fetched_at"],
            "normalized_count": len(normalized["normalized_items"]),
        }
    except PricingApiError as exc:
        json_error = exc.to_error_dict()
    except ValueError as exc:
        json_error = {
            "success": False,
            "error": str(exc),
        }
    except Exception as exc:
        json_error = {
            "success": False,
            "error": f"Pricing cache refresh failed: {exc}",
        }

    static_result: dict[str, Any] | None = None
    if settings.PRICING_ENABLE_SCRAPER_FALLBACK:
        static_result = await scrape_pricing_page()
        if (
            static_result.get("success")
            and _scraper_item_count(static_result) >= settings.PRICING_SCRAPER_MIN_ITEMS
        ):
            normalized = normalize_scraper_payload(static_result)
            await _write_pricing_cache(normalized)
            return {
                "success": True,
                "source": "html_scraper",
                "cache_key": PRICING_CACHE_KEY,
                "ttl_seconds": settings.PRICING_CACHE_TTL_SECONDS,
                "fetched_at": normalized["fetched_at"],
                "normalized_count": len(normalized["normalized_items"]),
                "warnings": static_result.get("warnings") or [],
            }

    playwright_result: dict[str, Any] | None = None
    if settings.PRICING_ENABLE_PLAYWRIGHT_FALLBACK:
        playwright_result = await scrape_all_pricing_pages_with_playwright()
        if (
            playwright_result.get("success")
            and _scraper_item_count(playwright_result) >= settings.PRICING_SCRAPER_MIN_ITEMS
        ):
            normalized = normalize_scraper_payload(playwright_result)
            await _write_pricing_cache(normalized)
            return {
                "success": True,
                "source": "playwright_html_scraper",
                "cache_key": PRICING_CACHE_KEY,
                "ttl_seconds": settings.PRICING_CACHE_TTL_SECONDS,
                "fetched_at": normalized["fetched_at"],
                "normalized_count": len(normalized["normalized_items"]),
                "warnings": playwright_result.get("warnings") or [],
            }

    error_message = "Pricing cache refresh failed: no valid JSON, static HTML, or Playwright data."
    if playwright_result and playwright_result.get("auth_required"):
        error_message = (
            "Pricing page requires authentication or session is not ready; "
            "cache was not updated."
        )
    elif playwright_result and playwright_result.get("error"):
        error_message = str(playwright_result["error"])
    elif static_result and static_result.get("error"):
        error_message = str(static_result["error"])
    elif json_error and json_error.get("error"):
        error_message = str(json_error["error"])

    return _build_refresh_failure(
        error=error_message,
        source=playwright_result.get("source")
        if playwright_result
        else static_result.get("source")
        if static_result
        else None,
        warnings=list(
            (playwright_result or {}).get("warnings")
            or (static_result or {}).get("warnings")
            or []
        ),
        json_attempt=json_error,
        static_attempt=static_result,
        playwright_attempt=playwright_result,
    )


async def get_cached_pricing() -> dict[str, Any]:
    try:
        redis_client = get_redis_client()
        cached_value = await redis_client.get(PRICING_CACHE_KEY)
    except Exception as exc:
        return {
            "cache_hit": False,
            "error": f"Redis read failed: {exc}",
        }

    if not cached_value:
        return {"cache_hit": False}

    try:
        parsed = json.loads(cached_value)
    except json.JSONDecodeError:
        return {
            "cache_hit": False,
            "error": "Cached pricing data is invalid JSON",
        }

    return {
        "cache_hit": True,
        "data": parsed,
    }
