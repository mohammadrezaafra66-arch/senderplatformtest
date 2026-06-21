"""Schemaهای request/response برای API."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from core_engine.models import AccountStatus, PlatformType


class ImportCommitRequest(BaseModel):
    file_path: str
    original_file_name: str
    stored_file_name: str
    sheet_name: str | None = None
    uploaded_by: str | None = None


class ImportCommitResponse(BaseModel):
    status: str
    import_batch_id: int
    total_rows: int
    created_contacts_count: int
    invalid_rows_count: int
    duplicate_rows_count: int
    errors_count: int
    message: str


class CampaignFromImportRequest(BaseModel):
    import_batch_id: int
    title: str
    platform: PlatformType
    template_text: str
    use_gpt: bool = False
    include_products: bool = False


class CampaignFromImportResponse(BaseModel):
    status: str
    campaign_id: int
    import_batch_id: int
    contacts_attached_count: int
    skipped_contacts_count: int
    message: str


class CampaignStatsData(BaseModel):
    """Stats درون Campaign detail response."""

    total_recipients: int
    queued: int
    processing: int
    sent: int
    failed: int
    progress_percent: float
    eta_seconds: int | None = None


class CampaignListItemResponse(BaseModel):
    """خلاصه Campaign برای لیست."""

    id: int
    name: str
    title: str
    platform: PlatformType
    status: str  # "draft", "prepared", "running", etc.
    created_at: datetime
    total_recipients: int


class CampaignDetailResponse(BaseModel):
    """جزئیات کامل Campaign."""

    id: int
    name: str
    title: str
    channel: str
    platform: PlatformType
    status: str
    template_text: str | None = None
    use_gpt: bool
    include_products: bool
    intent: str | None = None
    message_goal: str | None = None
    max_contacts: int | None = None
    daily_limit: int | None = None
    schedule_start_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    stats: CampaignStatsData


class CampaignsListResponse(BaseModel):
    """پاسخ لیست کمپین‌ها."""

    items: list[CampaignListItemResponse]
    total_count: int
    limit: int
    offset: int


class CampaignStartResponse(BaseModel):
    status: str
    campaign_id: int
    message: str
    bridge_result: dict[str, int | str] | None = None


class CampaignStopResponse(BaseModel):
    status: str
    campaign_id: int
    message: str
    paused_in_redis: bool


class CampaignRecipientItemResponse(BaseModel):
    """یک ردیف message log (گیرنده کمپین + مخاطب)."""

    id: int
    campaign_id: int
    contact_id: int
    phone: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    render_status: str
    send_status: str
    updated_at: datetime


class CampaignRecipientsListResponse(BaseModel):
    campaign_id: int
    items: list[CampaignRecipientItemResponse]
    total_count: int
    limit: int
    offset: int


class AccountResponse(BaseModel):
    """نمایش یک اکانت پیام‌رسان."""

    id: int
    platform: PlatformType
    account_identifier: str | None = None
    label: str | None = None
    status: AccountStatus
    proxy_url: str | None = None
    policy_id: int | None = None
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None


class AccountCreateRequest(BaseModel):
    platform: PlatformType
    account_identifier: str = Field(..., min_length=1, max_length=32)
    label: str | None = Field(default=None, max_length=255)
    proxy_url: str | None = Field(default=None, max_length=512)
    status: AccountStatus = AccountStatus.ACTIVE


class AccountCreateResponse(BaseModel):
    status: str
    account_id: int
    message: str


class AccountUpdateRequest(BaseModel):
    account_identifier: str | None = Field(default=None, min_length=1, max_length=32)
    label: str | None = Field(default=None, max_length=255)
    proxy_url: str | None = Field(default=None, max_length=512)
    status: AccountStatus | None = None


class AccountTestConnectionRequest(BaseModel):
    """بدنه اختیاری برای تست اتصال — فعلاً بدون فیلد اجباری."""

    force_fail: bool = False


class AccountTestConnectionResponse(BaseModel):
    success: bool
    account_id: int
    platform: PlatformType
    message: str
    error: str | None = None


class WhatsAppWebStatusResponse(BaseModel):
    account_id: int
    delivery_mode: str
    profile_dir: str
    profile_exists: bool
    session_registered: bool
    linked: bool
    needs_qr: bool
    phone: str | None = None
    linked_at: str | None = None
    message: str


class WhatsAppWebRegisterRequest(BaseModel):
    linked: bool = True
    phone: str | None = Field(default=None, max_length=32)


class WhatsAppWebRegisterResponse(BaseModel):
    success: bool
    account_id: int
    message: str
    profile_dir: str
    linked: bool


class WhatsAppWebPoolWorkerItem(BaseModel):
    hostname: str
    pool_size: int
    pool_index: int
    assigned_account_ids: list[int]
    updated_at: str | None = None


class WhatsAppWebPoolStatusResponse(BaseModel):
    workers: list[WhatsAppWebPoolWorkerItem]
    total: int


class AccountSessionStatusResponse(BaseModel):
    account_id: int
    platform: str
    account_status: str
    session_type: str
    session_registered: bool
    ready_for_delivery: bool
    message: str
    error: str | None = None
    delivery_mode: str | None = None
    linked: bool | None = None
    needs_qr: bool | None = None
    profile_exists: bool | None = None
    profile_dir: str | None = None
    linked_at: str | None = None


class AccountSessionRegisterRequest(BaseModel):
    """Plain bot token or JSON credentials (WhatsApp Cloud API)."""

    session_payload: str = Field(..., min_length=1, max_length=8192)


class AccountSessionRegisterResponse(BaseModel):
    success: bool
    account_id: int
    platform: PlatformType
    session_type: str
    message: str


class AccountSendTestRequest(BaseModel):
    """One-off operational test message (dry-run by default)."""

    message_text: str = Field(
        default="پیام تست عملیاتی — Sender Platform",
        min_length=1,
        max_length=4096,
    )
    recipient: str | None = Field(
        default=None,
        max_length=128,
        description="Phone (WhatsApp) or chat_id/@username (bot platforms).",
    )
    dry_run: bool = True
    confirm_live_send: bool = False


class AccountSendTestResponse(BaseModel):
    account_id: int
    platform: str
    dry_run: bool
    live_send: bool
    recipient: str
    recipient_type: str
    success: bool
    status: str
    platform_message_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    message: str


class LiveSendPreflightCheckItem(BaseModel):
    key: str
    passed: bool
    message: str


class LiveSendPreflightResponse(BaseModel):
    account_id: int
    platform: str
    ready_for_live_send: bool
    checks: list[LiveSendPreflightCheckItem]


class DeployReadinessWorkerService(BaseModel):
    name: str
    platform: str
    mode: str
    enabled_when: str | None = None


class DeployReadinessResponse(BaseModel):
    phase: str
    safety: dict[str, bool]
    dry_run: bool
    shadow_mode: bool
    whatsapp_delivery_mode: str
    operational_send: dict[str, bool]
    worker_services: list[DeployReadinessWorkerService]
    accounts_total: int
    active_accounts_total: int
    active_accounts_ready: int
    all_active_accounts_ready: bool
    accounts: list[AccountSessionStatusResponse]


class AccountsListResponse(BaseModel):
    items: list[AccountResponse]
    total_count: int


class KnowledgeBaseReadResponse(BaseModel):
    success: bool
    source: str | None = None
    content: str | None = None
    character_count: int | None = None
    error: str | None = None


class KnowledgeBaseContextResponse(BaseModel):
    success: bool
    context: str | None = None
    truncated: bool | None = None
    character_count: int | None = None
    max_chars: int | None = None
    error: str | None = None


class GenerateMessageRequest(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    customer_name: str | None = None
    customer_phone: str | None = None
    channel: str = "bale"
    goal: str = "معرفی محصول"
    message_goal: str | None = None
    intent: str | None = None
    include_products: bool = False
    max_kb_chars: int = 4000
    max_products: int = 3
    force_mock_output: bool = False

    @model_validator(mode="after")
    def normalize_customer_fields(self) -> "GenerateMessageRequest":
        if self.message_goal:
            object.__setattr__(self, "goal", self.message_goal)
        if not self.first_name and self.customer_name:
            parts = self.customer_name.strip().split(maxsplit=1)
            object.__setattr__(self, "first_name", parts[0])
            if len(parts) > 1:
                object.__setattr__(self, "last_name", parts[1])
        if not self.first_name:
            raise ValueError("first_name or customer_name is required")
        return self


class PersonalizedMessageOutput(BaseModel):
    greeting: str
    body: str
    cta: str
    product_block: str | None = None
    final_text: str
    warnings: list[str] = []


class MessageRenderDryRunRequest(GenerateMessageRequest):
    include_products: bool = True


class SaveRenderedMessageDryRunRequest(MessageRenderDryRunRequest):
    campaign_id: int | None = None
    contact_id: int | None = None

    def to_generate_request(self) -> GenerateMessageRequest:
        return GenerateMessageRequest.model_validate(self.model_dump())


class EncodingEchoRequest(BaseModel):
    customer_name: str | None = None
    message_goal: str | None = None
