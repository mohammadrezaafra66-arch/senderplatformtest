"""Deterministic recipient → variation assignment."""

from __future__ import annotations

import hashlib

from core_engine.services.message_variation.dto import FrozenVariation, FrozenVariationPool


def assign_variation_index(
    *,
    campaign_id: int,
    contact_id: int,
    generation_batch_id: str,
    pool_size: int,
) -> int:
    if pool_size <= 0:
        return 0
    material = f"{campaign_id}:{contact_id}:{generation_batch_id}".encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()
    return int(digest[:16], 16) % pool_size


def assign_variation(
    pool: FrozenVariationPool,
    *,
    campaign_id: int,
    contact_id: int,
) -> FrozenVariation:
    index = assign_variation_index(
        campaign_id=campaign_id,
        contact_id=contact_id,
        generation_batch_id=pool.generation_batch_id,
        pool_size=len(pool.variations),
    )
    return pool.variations[index]
