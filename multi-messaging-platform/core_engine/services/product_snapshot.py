"""ساخت و اعتبارسنجی ProductSnapshot از کش قیمت."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from core_engine.config import get_settings
from core_engine.models import ProductSnapshot
from core_engine.services.price_fetcher import get_cached_pricing


def _to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _to_naive_utc(value)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _to_naive_utc(parsed)


def compute_expires_at(
    *,
    fetched_at: datetime | None,
    created_at: datetime,
    validity_seconds: int | None = None,
) -> datetime:
    settings = get_settings()
    ttl_seconds = validity_seconds or settings.PRICING_CACHE_TTL_SECONDS
    base_time = fetched_at if fetched_at is not None else created_at
    return base_time + timedelta(seconds=ttl_seconds)


def is_snapshot_expired(snapshot: ProductSnapshot) -> bool:
    now = datetime.utcnow()
    return now > snapshot.expires_at


def _compute_payload_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _extract_item_count(normalized_payload: dict | list | None) -> int:
    if normalized_payload is None:
        return 0
    if isinstance(normalized_payload, list):
        return len(normalized_payload)
    if isinstance(normalized_payload, dict):
        for key in ("normalized_items", "items"):
            items = normalized_payload.get(key)
            if isinstance(items, list):
                return len(items)
    return 0


def _snapshot_to_metadata(snapshot: ProductSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.id,
        "payload_hash": snapshot.payload_hash,
        "item_count": _extract_item_count(snapshot.normalized_payload),
        "source_url": snapshot.source_url,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
        "fetched_at": snapshot.fetched_at.isoformat() if snapshot.fetched_at else None,
        "expires_at": snapshot.expires_at.isoformat() if snapshot.expires_at else None,
        "is_expired": is_snapshot_expired(snapshot),
    }


async def create_product_snapshot_from_cached_pricing(db: Session) -> dict[str, Any]:
    settings = get_settings()
    cache_result = await get_cached_pricing()

    if not cache_result.get("cache_hit"):
        return {
            "success": False,
            "error": cache_result.get("error", "Pricing cache miss"),
        }

    cached_data = cache_result["data"]
    if not isinstance(cached_data, dict):
        return {
            "success": False,
            "error": "Cached pricing data has invalid structure",
        }

    normalized_items = cached_data.get("normalized_items")
    if not isinstance(normalized_items, list):
        normalized_items = []

    fetched_at = _parse_datetime(cached_data.get("fetched_at"))
    created_at = datetime.utcnow()
    expires_at = compute_expires_at(
        fetched_at=fetched_at,
        created_at=created_at,
        validity_seconds=settings.PRICING_CACHE_TTL_SECONDS,
    )

    raw_payload = cached_data.get("raw_payload", cached_data)
    normalized_payload = {
        "source": cached_data.get("source"),
        "fetched_at": cached_data.get("fetched_at"),
        "normalized_items": normalized_items,
    }
    payload_hash = _compute_payload_hash(normalized_payload)

    try:
        snapshot = ProductSnapshot(
            source_url=settings.PRICING_API_URL,
            fetched_at=fetched_at,
            raw_payload=raw_payload if isinstance(raw_payload, dict) else {"data": raw_payload},
            normalized_payload=normalized_payload,
            payload_hash=payload_hash,
            created_at=created_at,
            expires_at=expires_at,
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)

        return {
            "success": True,
            "snapshot_id": snapshot.id,
            "payload_hash": snapshot.payload_hash,
            "item_count": len(normalized_items),
            "source_url": snapshot.source_url,
            "created_at": snapshot.created_at.isoformat(),
            "fetched_at": snapshot.fetched_at.isoformat() if snapshot.fetched_at else None,
            "expires_at": snapshot.expires_at.isoformat(),
            "validity_seconds": settings.PRICING_CACHE_TTL_SECONDS,
        }
    except Exception as exc:
        db.rollback()
        return {
            "success": False,
            "error": f"Failed to create product snapshot: {exc}",
        }


def get_latest_product_snapshot(db: Session) -> dict[str, Any]:
    snapshot = (
        db.query(ProductSnapshot)
        .order_by(ProductSnapshot.id.desc())
        .first()
    )
    if not snapshot:
        return {"found": False}

    result = {"found": True}
    result.update(_snapshot_to_metadata(snapshot))
    return result


def get_latest_valid_product_snapshot(db: Session) -> dict[str, Any]:
    snapshot = (
        db.query(ProductSnapshot)
        .order_by(ProductSnapshot.id.desc())
        .first()
    )
    if not snapshot:
        return {
            "found": False,
            "reason": "No product snapshot found",
        }

    if is_snapshot_expired(snapshot):
        return {
            "found": False,
            "reason": "Latest product snapshot is expired",
            "snapshot_id": snapshot.id,
            "expired_at": snapshot.expires_at.isoformat(),
        }

    metadata = _snapshot_to_metadata(snapshot)
    return {
        "found": True,
        "snapshot_id": metadata["snapshot_id"],
        "payload_hash": metadata["payload_hash"],
        "item_count": metadata["item_count"],
        "expires_at": metadata["expires_at"],
        "is_expired": False,
    }
