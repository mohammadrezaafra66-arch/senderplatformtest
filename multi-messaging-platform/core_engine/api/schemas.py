"""Schemaهای request/response برای API."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PositiveInt, model_validator

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
    account_ids: list[PositiveInt] | None = None

    @model_validator(mode="after")
    def deduplicate_account_ids(self) -> "CampaignFromImportRequest":
        if self.account_ids is not None:
            object.__setattr__(self, "account_ids", list(dict.fromkeys(self.account_ids)))
        return self


class CampaignFromContactsRequest(BaseModel):
    contact_ids: list[PositiveInt] = Field(..., min_length=1)
    title: str
    platform: PlatformType
    template_text: str
    use_gpt: bool = False
    include_products: bool = False
    account_ids: list[PositiveInt] | None = None

    @model_validator(mode="after")
    def deduplicate_contacts_and_accounts(self) -> "CampaignFromContactsRequest":
        # Deterministic de-dupe for contacts.
        object.__setattr__(self, "contact_ids", sorted(set(self.contact_ids)))

        # Keep "first occurrence" order for sender account lists.
        if self.account_ids is not None:
            object.__setattr__(self, "account_ids", list(dict.fromkeys(self.account_ids)))

        return self


class CampaignFromTagsRequest(BaseModel):
    selected_tags: list[str] = Field(..., min_length=1)
    tag_match: Literal["any", "all"] = "any"
    title: str
    platform: PlatformType
    template_text: str
    use_gpt: bool = False
    include_products: bool = False
    account_ids: list[PositiveInt] | None = None

    @model_validator(mode="after")
    def normalize_selected_tags(self) -> "CampaignFromTagsRequest":
        from core_engine.services.contact_tags import read_contact_tags

        tags = read_contact_tags(self.selected_tags)
        if not tags:
            raise ValueError("At least one non-empty tag is required.")
        object.__setattr__(self, "selected_tags", tags)
        if self.account_ids is not None:
            object.__setattr__(self, "account_ids", list(dict.fromkeys(self.account_ids)))
        return self


class CampaignSkippedContactInfo(BaseModel):
    contact_id: int
    reason_code: str


class CampaignAutoPrepareSummary(BaseModel):
    attempted: bool = False
    prepared: bool = False
    skipped: bool = False
    skip_reason: str | None = None
    blockers: list[dict] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    ready_count: int | None = None
    staged_count: int | None = None


class AutomaticSenderAssignmentSummary(BaseModel):
    mode: str = "automatic"
    eligible_accounts: int = 0
    existing_assignments: int = 0
    created_assignments: int = 0
    skipped_assignments: int = 0
    excluded_accounts: int = 0
    reason: str | None = None
    eligible_account_ids: list[int] = Field(default_factory=list)
    created_account_ids: list[int] = Field(default_factory=list)


class AudiencePreviewRequest(BaseModel):
    selected_tags: list[str] = Field(..., min_length=1)
    tag_match: Literal["any", "all"] = "any"


class AudiencePreviewResponse(BaseModel):
    selected_tags: list[str]
    tag_match: str
    eligible_count: int
    skipped_count: int
    contact_ids: list[int]


class CampaignFromTagsResponse(BaseModel):
    status: str
    campaign_id: int
    selected_tags: list[str]
    tag_match: str
    contacts_attached_count: int
    skipped_contacts_count: int
    message: str
    account_ids: list[int] = Field(default_factory=list)
    sender_accounts: list["SenderAccountResponse"] = Field(default_factory=list)
    auto_prepare: CampaignAutoPrepareSummary | None = None
    sender_assignment: AutomaticSenderAssignmentSummary | None = None


class CampaignFromContactsResponse(BaseModel):
    status: str
    campaign_id: int
    contacts_attached_count: int
    skipped_contacts_count: int
    message: str
    account_ids: list[int] = Field(default_factory=list)
    sender_accounts: list["SenderAccountResponse"] = Field(default_factory=list)
    skipped_contacts: list[CampaignSkippedContactInfo] = Field(default_factory=list)
    auto_prepare: CampaignAutoPrepareSummary | None = None
    sender_assignment: AutomaticSenderAssignmentSummary | None = None


class ContactSearchItemResponse(BaseModel):
    contact_id: int
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    phone: str
    consent_status: str
    blacklisted: bool
    eligible: bool
    ineligible_reason: str | None = None


class ContactListItemResponse(BaseModel):
    contact_id: int
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    phone: str
    consent_status: str
    blacklisted: bool
    eligible: bool
    ineligible_reason: str | None = None
    created_at: datetime
    source_import_id: int | None = None
    source_import_file_name: str | None = None
    source_imported_at: datetime | None = None
    import_count: int = 0
    campaign_count: int = 0
    tags: list[str] = Field(default_factory=list)


class ContactsListResponse(BaseModel):
    items: list[ContactListItemResponse]
    total_count: int
    limit: int
    offset: int


class ContactDeleteResponse(BaseModel):
    success: bool
    contact_id: int
    already_deleted: bool
    deleted_at: datetime | None = None
    message: str


class ContactUpdateRequest(BaseModel):
    """Manual edit. tags, when present, replaces the tag list. Import does not use this."""

    first_name: str | None = None
    last_name: str | None = None
    tags: list[str] | None = None


class ContactUpdateResponse(BaseModel):
    contact_id: int
    first_name: str | None = None
    last_name: str | None = None
    tags: list[str] = Field(default_factory=list)


class ContactTagsResponse(BaseModel):
    tags: list[str] = Field(default_factory=list)


class ContactsSearchResponse(BaseModel):
    items: list[ContactSearchItemResponse]
    total_count: int
    limit: int
    offset: int


class GptPreviewRequest(BaseModel):
    """Operator GPT preview. Provider secrets are never accepted from the client."""

    model_config = ConfigDict(extra="forbid")

    template_text: str
    include_products: bool = False
    requested_count: int | None = Field(default=None, ge=1, le=3)


class CampaignRenderPreviewRequest(BaseModel):
    """Sample composition preview. Backend owns final text; client secrets are forbidden."""

    model_config = ConfigDict(extra="forbid")

    template_text: str
    platform: PlatformType | None = None
    use_gpt: bool = False
    include_products: bool = False
    preview_count: int | None = Field(default=3, ge=1, le=5)
    preview_variables: dict[str, str] | None = None


class SenderAccountResponse(BaseModel):
    account_id: int
    label: str | None = None
    account_identifier: str | None = None
    display_identity: str | None = None
    platform: PlatformType
    status: AccountStatus
    priority: int
    weight: int
    enabled: bool
    # C1 — L18-backed campaign eligibility (assigned ≠ ready)
    runtime_status: str | None = None
    runtime_status_label: str | None = None
    campaign_eligible: bool | None = None
    blocker_code: str | None = None
    blocker_label: str | None = None
    auth_ready: bool | None = None
    worker_ready: bool | None = None
    dispatch_ready: bool | None = None


class CampaignAccountsUpdateRequest(BaseModel):
    account_ids: list[PositiveInt]

    @model_validator(mode="after")
    def deduplicate_account_ids(self) -> "CampaignAccountsUpdateRequest":
        object.__setattr__(self, "account_ids", list(dict.fromkeys(self.account_ids)))
        return self


class CampaignAccountsResponse(BaseModel):
    campaign_id: int
    account_ids: list[int]
    sender_accounts: list[SenderAccountResponse]
    assignment_warnings: list[dict[str, str | int | None]] = Field(default_factory=list)
    auto_prepare: CampaignAutoPrepareSummary | None = None


class CampaignFromImportResponse(BaseModel):
    status: str
    campaign_id: int
    import_batch_id: int
    contacts_attached_count: int
    skipped_contacts_count: int
    message: str
    account_ids: list[int] = Field(default_factory=list)
    sender_accounts: list[SenderAccountResponse] = Field(default_factory=list)
    auto_prepare: CampaignAutoPrepareSummary | None = None
    sender_assignment: AutomaticSenderAssignmentSummary | None = None


class CampaignStatsData(BaseModel):
    """Stats درون Campaign detail response."""

    total_recipients: int
    queued: int
    processing: int
    sent: int
    failed: int
    progress_percent: float
    eta_seconds: int | None = None


class CommittedRenderSampleResponse(BaseModel):
    """Exact persisted RenderedMessage sample (not regenerated)."""

    rendered_message_id: int
    contact_id: int | None = None
    recipient_name: str | None = None
    sender_account_id: int | None = None
    final_text: str
    final_text_sha256: str | None = None
    render_batch_id: str | None = None
    render_version: str | None = None
    use_gpt: bool = False
    variation_id: str | None = None
    include_products: bool = False
    product_count: int = 0
    rendered_at: datetime | None = None
    committed: bool = True
    label: str = "پیام نهایی ثبت‌شده"


class CampaignListItemResponse(BaseModel):
    """خلاصه Campaign برای لیست."""

    id: int
    name: str
    title: str
    platform: PlatformType
    status: str  # "draft", "prepared", "running", etc.
    created_at: datetime
    total_recipients: int
    account_ids: list[int] = Field(default_factory=list)
    sender_accounts: list[SenderAccountResponse] = Field(default_factory=list)
    archived_at: datetime | None = None
    archived_by: str | None = None


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
    effective_cap: int | None = None
    unlimited: bool = True
    daily_limit: int | None = None
    stop_label: str | None = None
    schedule_start_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    stats: CampaignStatsData
    account_ids: list[int] = Field(default_factory=list)
    sender_accounts: list[SenderAccountResponse] = Field(default_factory=list)
    latest_render_batch_id: str | None = None
    render_version: str | None = None
    committed_renders: list[CommittedRenderSampleResponse] = Field(default_factory=list)
    archived_at: datetime | None = None
    archived_by: str | None = None
    archive_reason: str | None = None


class CampaignsListResponse(BaseModel):
    """پاسخ لیست کمپین‌ها."""

    items: list[CampaignListItemResponse]
    total_count: int
    limit: int
    offset: int


class CampaignStartRequest(BaseModel):
    """Explicit operator approval required when controlled production mode is on."""

    confirm_controlled_production: bool = False


class CampaignStartResponse(BaseModel):
    status: str
    campaign_id: int
    message: str
    bridge_result: dict[str, int | str] | None = None
    preflight: dict | None = None
    request_id: str | None = None
    accepted: bool = True
    campaign_status: str | None = None
    queue_jobs_created: int | None = None
    messages_scheduled: int | None = None
    controlled_confirmation_accepted: bool = False


class CampaignPreflightResponse(BaseModel):
    allowed_to_start: bool
    code: str
    message: str
    campaign_id: int
    execution_safety_state: str
    total_messages: int
    ready_messages: int
    blocked_messages: int
    assigned_accounts: int
    usable_accounts: int
    blocked_accounts: int
    temporary_accounts: int = 0
    immediate_capacity: int | None = None
    estimated_today_capacity: int | None = None
    estimated_hourly_capacity: int | None = None
    estimated_completion_at: str | None = None
    estimated_duration_seconds: int | None = None
    timezone: str = "Asia/Tehran"
    warnings: list[dict] = Field(default_factory=list)
    blockers: list[dict] = Field(default_factory=list)
    accounts: list[dict] = Field(default_factory=list)
    progress: dict[str, int] = Field(default_factory=dict)
    sender_selection_mode: str = "automatic"
    delivery_mode: str | None = None
    circuit_state: str | None = None
    next_window_start: str | None = None
    resume_policy: str = "operator"
    ready_to_resume: bool = False
    capacity_confidence: str | None = None
    limitations: list[str] = Field(default_factory=list)
    evaluated_at: str = ""
    redis_ok: bool = True
    redis_available: bool = True
    capacity_known: bool = True
    ready_accounts: int = 0
    execution_usable_accounts: int = 0
    campaign_eligible_accounts: int = 0
    authenticated_accounts: int = 0
    worker_ready_accounts: int = 0
    assignment_materialized: bool = False
    capacity_applicable: bool = False
    campaign_prepared: bool = False
    prepared_messages: int = 0
    preparation_ready: bool = True
    preparation_blockers: list[dict] = Field(default_factory=list)
    technical_ready: bool = False
    controlled_production_enabled: bool = False
    controlled_production_confirmation_required: bool = False
    allowed_to_start_after_confirmation: bool = False
    controlled_production_max_messages: int | None = None
    effective_cap: int | None = None
    unlimited: bool = True
    controlled_production_label: str | None = None


class CampaignPrepareRequest(BaseModel):
    force_mock_output: bool = False
    limit: int | None = None


class CampaignPrepareResponse(BaseModel):
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
    message: str = "Campaign messages prepared."


class CampaignReprepareResponse(BaseModel):
    campaign_id: int
    reset_unsent_staged: int
    ready_count: int
    staged_count: int
    allowed_contacts: int
    total_contacts: int
    real_gpt_called: bool = False
    redis_queue_pushed: bool = False
    status: str


class CampaignStopResponse(BaseModel):
    status: str
    campaign_id: int
    message: str
    paused_in_redis: bool
    inflight: int = 0
    fully_stopped: bool = True


class MessageSenderAccountResponse(BaseModel):
    account_id: int
    label: str | None = None
    account_identifier: str | None = None
    platform: PlatformType
    status: AccountStatus


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
    failure_reason: str | None = None
    final_message_id: int | None = None
    account_id: int | None = None
    sender_account: MessageSenderAccountResponse | None = None
    updated_at: datetime
    final_text_preview: str | None = None
    has_more: bool = False
    has_long_text: bool = False
    use_gpt: bool | None = None
    include_products: bool | None = None
    variation_id: str | None = None
    product_count: int | None = None
    render_batch_id: str | None = None
    render_version: str | None = None
    final_text_sha256: str | None = None


class CampaignRecipientDetailResponse(CampaignRecipientItemResponse):
    """Full message-log trace. Never includes secrets or raw provider payloads."""

    rendered_message_id: int | None = None
    message_id: int | None = None
    attempt_no: int | None = None
    platform: str | None = None
    final_text: str | None = None
    gpt: dict | None = None
    products: dict | None = None
    error_code: str | None = None
    platform_message_id: str | None = None
    delivery_state: str = "unknown"
    read_state: str = "unknown"
    rendered_at: datetime | None = None
    sent_at: datetime | None = None
    created_at: datetime | None = None


class CampaignRecipientsListResponse(BaseModel):
    campaign_id: int
    items: list[CampaignRecipientItemResponse]
    total_count: int
    limit: int
    offset: int


class AccountRuntimeAuthBlock(BaseModel):
    state: str
    reason: str | None = None


class AccountRuntimeCredentialBlock(BaseModel):
    type: str | None = None
    state: str


class AccountRuntimeIdentityBlock(BaseModel):
    state: str


class AccountRuntimeWorkerBlock(BaseModel):
    state: str
    covered: bool | None = None
    heartbeat_fresh: bool | None = None


class AccountRuntimeDispatchBlock(BaseModel):
    ready: bool
    blocker: str | None = None


class AccountRuntimeOperatorActionBlock(BaseModel):
    code: str
    label: str


class AccountRuntimeBlock(BaseModel):
    """L18 authoritative runtime truth — separate from Account.status lifecycle."""

    runtime_status: str
    runtime_status_label: str
    enabled: bool
    auth: AccountRuntimeAuthBlock
    credential: AccountRuntimeCredentialBlock
    identity: AccountRuntimeIdentityBlock
    worker: AccountRuntimeWorkerBlock
    dispatch: AccountRuntimeDispatchBlock
    operator_action: AccountRuntimeOperatorActionBlock
    reason_code: str
    last_verified_at: datetime | str | None = None


class AccountResponse(BaseModel):
    """نمایش یک اکانت پیام‌رسان."""

    id: int
    platform: PlatformType
    account_identifier: str | None = None
    label: str | None = None
    display_identity: str | None = None
    status: AccountStatus
    proxy_url: str | None = None
    policy_id: int | None = None
    hourly_message_limit: int | None = None
    daily_message_limit: int | None = None
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None
    archived_at: datetime | None = None
    archived_by: str | None = None
    archive_reason: str | None = None
    # L18 — connection/auth/worker truth (optional for backward compatibility)
    runtime: AccountRuntimeBlock | None = None
    runtime_status: str | None = None
    runtime_status_label: str | None = None
    account_enabled: bool | None = None
    # C1 — same base predicate as campaign picker / auto-select
    campaign_eligible: bool | None = None
    campaign_blocker_code: str | None = None
    campaign_blocker_label: str | None = None
    campaign_status_label: str | None = None


class ArchiveActionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=512)


class ArchiveActionResponse(BaseModel):
    status: str
    entity_type: str
    entity_id: int
    already_archived: bool = False
    already_active: bool = False
    archived_at: datetime | None = None
    restored_at: datetime | None = None
    previous_status: str | None = None
    queued_items_cancelled: int = 0
    pending_recipients_stopped: int = 0
    already_sent_count: int = 0
    in_flight_count: int = 0
    message: str
    details: dict = Field(default_factory=dict)


def _optional_positive_message_limit(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError("must be a positive integer or null")
    return value


OptionalPositiveMessageLimit = Annotated[
    int | None,
    BeforeValidator(_optional_positive_message_limit),
]


class AccountCreateRequest(BaseModel):
    platform: PlatformType
    account_identifier: str = Field(..., min_length=1, max_length=32)
    label: str | None = Field(default=None, max_length=255)
    proxy_url: str | None = Field(default=None, max_length=512)
    status: AccountStatus = AccountStatus.ACTIVE
    hourly_message_limit: OptionalPositiveMessageLimit = None
    daily_message_limit: OptionalPositiveMessageLimit = None


class AccountCreateResponse(BaseModel):
    status: str
    account_id: int
    message: str


class AccountUpdateRequest(BaseModel):
    account_identifier: str | None = Field(default=None, min_length=1, max_length=32)
    label: str | None = Field(default=None, max_length=255)
    proxy_url: str | None = Field(default=None, max_length=512)
    status: AccountStatus | None = None
    hourly_message_limit: OptionalPositiveMessageLimit = None
    daily_message_limit: OptionalPositiveMessageLimit = None


class AccountTestConnectionRequest(BaseModel):
    """بدنه اختیاری برای تست اتصال — فعلاً بدون فیلد اجباری."""

    force_fail: bool = False


class AccountTestConnectionResponse(BaseModel):
    success: bool
    account_id: int
    platform: PlatformType
    message: str
    error: str | None = None
    status: str | None = None
    reason_code: str | None = None
    verified_at: datetime | str | None = None
    runtime_status: str | None = None
    runtime_status_label: str | None = None


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
    code: str | None = None
    delivery_mode: str | None = None
    linked: bool | None = None
    needs_qr: bool | None = None
    profile_exists: bool | None = None
    profile_dir: str | None = None
    linked_at: str | None = None
    # L18 safe credential metadata (never secrets)
    credential_type: str | None = None
    credential_state: str | None = None
    runtime_status: str | None = None
    runtime_status_label: str | None = None
    requires_relogin: bool | None = None
    last_verified_at: datetime | str | None = None
    session_id: int | None = None  # safe metadata id only


class AccountSessionRegisterRequest(BaseModel):
    """Plain bot token or JSON credentials (WhatsApp Cloud API)."""

    session_payload: str = Field(..., min_length=1, max_length=8192)


class AccountSessionRegisterResponse(BaseModel):
    success: bool
    account_id: int
    platform: PlatformType
    session_type: str
    message: str


class RubikaUserLoginStartRequest(BaseModel):
    """شروع ورود تعاملی روبیکا. برای ادامه pass_key، registration_token را همراه پر کن."""

    phone_number: str | None = Field(default=None, max_length=20)
    pass_key: str | None = Field(default=None, max_length=64)
    registration_token: str | None = Field(default=None, max_length=128)


class RubikaUserLoginStartResponse(BaseModel):
    registration_token: str
    stage: str  # "code_required" | "pass_key_required"
    message: str
    hint_pass_key: str | None = None
    state: str | None = None
    code: str | None = None
    retry_after_seconds: int | None = None
    resend_available_at: str | None = None


class RubikaUserLoginVerifyRequest(BaseModel):
    registration_token: str = Field(..., min_length=1, max_length=128)
    phone_code: str = Field(..., min_length=1, max_length=16)


class RubikaUserLoginVerifyResponse(BaseModel):
    success: bool
    account_id: int
    guid: str
    phone_number: str
    message: str
    lifecycle_status: str | None = None
    runtime_status: str | None = None
    runtime_status_label: str | None = None
    send_activation_state: str | None = None
    activation_confirm_code: str | None = None


class RubikaActivationStatusResponse(BaseModel):
    account_id: int
    send_activation_state: str
    status: str | None = None
    confirm_code: str | None = None
    manager_phone: str | None = None
    test_sent_at: datetime | None = None
    test_error: str | None = None
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None


class RubikaActivationConfirmRequest(BaseModel):
    token: str | None = Field(default=None, max_length=128)
    confirm_code: str | None = Field(default=None, max_length=16)


class RubikaActivationConfirmResponse(BaseModel):
    success: bool
    account_id: int
    code: str
    send_activation_state: str
    message: str
    runtime_status: str | None = None
    runtime_status_label: str | None = None


# ─── فاز ۴ — Pool ───


class RubikaPoolAccountItem(BaseModel):
    account_id: int
    label: str | None = None
    phone_number: str | None = None
    account_status: str
    phase: str
    priority: int
    last_error_at: datetime | None = None
    last_error_message: str | None = None
    last_used_at: datetime | None = None


class RubikaAccountsListResponse(BaseModel):
    items: list[RubikaPoolAccountItem]
    total_count: int


class RubikaPoolUpsertRequest(BaseModel):
    phase: str = Field(..., pattern="^(day|night|listener|status)$")
    priority: int = Field(default=1, ge=1, le=100)


class RubikaPoolUpsertResponse(BaseModel):
    success: bool
    account_id: int
    phase: str
    priority: int
    message: str


class RubikaPoolRestoreResponse(BaseModel):
    success: bool
    account_id: int
    account_status: str
    message: str


# ─── فاز ۴ — لاگ ارسال ───


class RubikaSendLogItem(BaseModel):
    message_id: int
    campaign_id: int
    campaign_title: str | None = None
    account_id: int
    account_label: str | None = None
    contact_id: int
    contact_phone: str | None = None
    rendered_text: str | None = None
    status: str | None = None
    platform_message_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime


class RubikaSendLogResponse(BaseModel):
    items: list[RubikaSendLogItem]
    total_count: int
    limit: int
    offset: int


# ─── فاز ۴ — گروه‌ها ───


class RubikaGroupCreateRequest(BaseModel):
    group_guid: str = Field(..., min_length=1, max_length=255)
    group_name: str | None = Field(default=None, max_length=512)
    listener_account_id: int | None = None
    keywords: list[str] = Field(default_factory=list)
    keyword_response: str | None = None
    red_keywords: list[str] = Field(default_factory=list)
    conversation_mode_enabled: bool = False


class RubikaGroupUpdateRequest(BaseModel):
    group_name: str | None = None
    listener_account_id: int | None = None
    keywords: list[str] | None = None
    keyword_response: str | None = None
    red_keywords: list[str] | None = None
    conversation_mode_enabled: bool | None = None
    is_active: bool | None = None


class RubikaGroupResponse(BaseModel):
    id: int
    group_guid: str
    group_name: str | None = None
    listener_account_id: int | None = None
    keywords: list[str] = Field(default_factory=list)
    keyword_response: str | None = None
    red_keywords: list[str] = Field(default_factory=list)
    conversation_mode_enabled: bool
    is_active: bool
    created_at: datetime


class RubikaGroupsListResponse(BaseModel):
    items: list[RubikaGroupResponse]
    total_count: int


class RubikaGroupMessageItem(BaseModel):
    id: int
    sender_name: str | None = None
    sender_phone: str | None = None
    message_type: str
    message_text: str | None = None
    transcription: str | None = None
    image_extracted_text: str | None = None
    is_reply_to_our_message: bool
    has_red_keyword: bool
    received_at: datetime


class RubikaGroupMessagesResponse(BaseModel):
    group_id: int
    items: list[RubikaGroupMessageItem]
    total_count: int
    limit: int
    offset: int


# ─── فاز ۴ — زمان‌بندی روز/شب ───


class RubikaScheduleItem(BaseModel):
    phase: str
    slot: int
    start_hour: int
    end_hour: int
    max_per_hour: int
    is_active: bool


class RubikaScheduleListResponse(BaseModel):
    items: list[RubikaScheduleItem]


class RubikaScheduleUpdateRequest(BaseModel):
    start_hour: int = Field(..., ge=0, le=24)
    end_hour: int = Field(..., ge=0, le=24)
    max_per_hour: int = Field(..., ge=1, le=1000)
    is_active: bool = True


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
    delivery_mode: str | None = None
    user_account_enabled: bool | None = None


class DeployReadinessResponse(BaseModel):
    phase: str
    safety: dict[str, bool]
    dry_run: bool
    shadow_mode: bool
    whatsapp_delivery_mode: str
    rubika_delivery_mode: str | None = None
    rubika_user_account_enabled: bool | None = None
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
