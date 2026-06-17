"""Schemaهای request/response برای API."""

from pydantic import BaseModel, model_validator

from core_engine.models import PlatformType


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
