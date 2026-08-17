"""GPT message variation engine (Phase 5.6)."""

from core_engine.services.message_variation.assignment import assign_variation
from core_engine.services.message_variation.dto import FrozenVariationPool
from core_engine.services.message_variation.errors import GptVariationError
from core_engine.services.message_variation.service import (
    assign_and_log,
    assignment_metadata,
    build_gpt_preview,
    generate_validated_pool,
    get_message_variation_provider,
    gpt_status,
    pool_from_queue_payload,
    set_message_variation_provider,
)

__all__ = [
    "FrozenVariationPool",
    "GptVariationError",
    "assign_and_log",
    "assign_variation",
    "assignment_metadata",
    "build_gpt_preview",
    "generate_validated_pool",
    "get_message_variation_provider",
    "gpt_status",
    "pool_from_queue_payload",
    "set_message_variation_provider",
]
