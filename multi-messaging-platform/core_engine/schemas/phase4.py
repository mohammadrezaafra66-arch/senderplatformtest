"""Phase 4 request/response schemas for campaign, contact, and staging."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core_engine.services.phase4_utils import validate_campaign_channel

PHASE4_CHANNELS = frozenset({"whatsapp", "telegram", "rubika", "bale"})
PHASE4_CONSENT_VALUES = frozenset({"allowed", "blocked", "unknown"})


class CampaignCreateRequest(BaseModel):
    name: str
    channel: str
    intent: str | None = None
    message_goal: str | None = None
    daily_limit: int | None = None
    max_contacts: int | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name is required and must be non-empty")
        return stripped

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, value: str) -> str:
        return validate_campaign_channel(value)

    @field_validator("daily_limit")
    @classmethod
    def validate_daily_limit(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("daily_limit must be positive when provided")
        return value

    @field_validator("max_contacts")
    @classmethod
    def validate_max_contacts(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("max_contacts must be positive when provided")
        return value


class CampaignDebugResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    channel: str
    intent: str | None = None
    message_goal: str | None = None
    status: str
    daily_limit: int | None = None
    max_contacts: int | None = None
    created_at: datetime
    updated_at: datetime


class ContactImportItem(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str
    channel_handle: str | None = None
    consent_status: str = "unknown"
    tags: dict[str, Any] | list[Any] | None = None
    raw_payload: dict[str, Any] | None = None


class ContactImportRequest(BaseModel):
    campaign_id: int
    contacts: list[ContactImportItem]

    @field_validator("campaign_id")
    @classmethod
    def validate_campaign_id(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("campaign_id is required")
        return value

    @model_validator(mode="after")
    def validate_contacts_not_empty(self) -> ContactImportRequest:
        if not self.contacts:
            raise ValueError("contacts must not be empty")
        return self


class ContactDebugResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    phone: str
    channel_handle: str | None = None
    consent_status: str
    tags: dict[str, Any] | list[Any] | None = None
    raw_payload: dict[str, Any] | None = None
    created_at: datetime


class ContactImportResultResponse(BaseModel):
    campaign_id: int
    received_count: int
    imported_count: int
    duplicate_count: int
    invalid_count: int
    allowed_count: int
    blocked_count: int
    unknown_count: int
    skipped_duplicates: list[dict[str, Any]] = Field(default_factory=list)
    invalid_items: list[dict[str, Any]] = Field(default_factory=list)
    contacts: list[ContactDebugResponse] = Field(default_factory=list)


class PrepareMessagesRequest(BaseModel):
    force_mock_output: bool = True
    limit: int | None = None

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("limit must be positive when provided")
        return value


class PrepareMessagesResultResponse(BaseModel):
    campaign_id: int
    total_contacts: int
    allowed_contacts: int
    skipped_contacts: int
    staged_count: int
    ready_count: int
    blocked_count: int
    already_staged_count: int
    limit_applied: int | None = None
    product_snapshot_id: int | None = None
    product_snapshot_valid: bool
    force_mock_output: bool
    real_gpt_called: bool = False
    real_queue_push_enabled: bool
    redis_queue_pushed: bool = False
    items: list[StagedQueueItemResponse] = Field(default_factory=list)


class StagedQueueItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    contact_id: int
    rendered_message_id: int | None = None
    channel: str
    status: str
    final_text: str
    queue_payload: dict[str, Any]
    skip_reason: str | None = None
    created_at: datetime


class StagedMessagesSummaryResponse(BaseModel):
    campaign_id: int
    total_contacts: int
    allowed_contacts: int
    skipped_contacts: int
    staged_count: int
    ready_count: int
    blocked_count: int
    items: list[StagedQueueItemResponse] = Field(default_factory=list)


class QueueStatusResponse(BaseModel):
    real_queue_push_enabled: bool
    redis_queue_lengths: dict[str, int]
    staged_items_count: int
    ready_staged_items_count: int = 0
    campaigns_prepared_count: int = 0
    safety_status: dict[str, bool] = Field(default_factory=dict)
    redis_inspection_note: str | None = None
