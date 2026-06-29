"""مدل‌های ORM پایگاه داده."""

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core_engine.database import Base


class PlatformType(str, enum.Enum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    RUBIKA = "rubika"
    BALE = "bale"


class AccountStatus(str, enum.Enum):
    ACTIVE = "active"
    RESTING = "resting"
    BANNED = "banned"
    REQUIRES_LOGIN = "requires_login"


class ConsentStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    ALLOWED = "allowed"
    BLOCKED = "blocked"


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    PREPARED = "prepared"
    PAUSED = "paused"
    ARCHIVED = "archived"
    # Legacy statuses retained for existing campaign rows.
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StagedQueueItemStatus(str, enum.Enum):
    STAGED = "staged"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    READY = "ready"
    PUSHING = "pushing"
    QUEUED = "queued"


class RenderStatus(str, enum.Enum):
    PENDING = "pending"
    RENDERED = "rendered"
    FAILED = "failed"


class SendStatus(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    ACCEPTED_BY_WORKER = "accepted_by_worker"
    ACCEPTED_BY_PLATFORM = "accepted_by_platform"
    DELIVERED = "delivered"
    READ = "read"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_PERMANENT = "failed_permanent"
    DRY_RUN = "dry_run"
    SHADOW_SENT = "shadow_sent"
    OPTED_OUT = "opted_out"
    BLACKLISTED = "blacklisted"


class MessageAttemptStatus(str, enum.Enum):
    STARTED = "started"
    SUCCESS = "success"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_PERMANENT = "failed_permanent"
    DRY_RUN = "dry_run"
    SHADOW_SENT = "shadow_sent"


class SessionType(str, enum.Enum):
    API_TOKEN = "api_token"
    MTPROTO_SESSION = "mtproto_session"
    BROWSER_PROFILE = "browser_profile"
    STRING_SESSION = "string_session"
    EVOLUTION_INSTANCE = "evolution_instance"  # جدید — برای Evolution API


class ImportStatus(str, enum.Enum):
    PENDING = "pending"
    PREVIEWED = "previewed"
    VALIDATED = "validated"
    COMMITTED = "committed"
    FAILED = "failed"


class ImportRowStatus(str, enum.Enum):
    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"
    DUPLICATE = "duplicate"
    SKIPPED = "skipped"


class RoleType(str, enum.Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (Index("ix_accounts_platform_status", "platform", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[PlatformType] = mapped_column(Enum(PlatformType), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus),
        nullable=False,
        default=AccountStatus.ACTIVE,
    )
    proxy_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    policy_id: Mapped[int | None] = mapped_column(
        ForeignKey("rate_policies.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    evolution_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    policy: Mapped["RatePolicy | None"] = relationship(
        "RatePolicy",
        back_populates="accounts",
        foreign_keys=[policy_id],
    )
    channel_sessions: Mapped[list["ChannelSession"]] = relationship(
        "ChannelSession",
        back_populates="account",
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="account",
    )


class AccountSendSettings(Base):
    __tablename__ = "account_send_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id"), nullable=False, unique=True
    )
    min_delay_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=45)
    max_delay_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    floor_delay_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    account: Mapped["Account"] = relationship(
        "Account", backref="send_settings", uselist=False
    )


class ChannelSession(Base):
    __tablename__ = "channel_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id"),
        nullable=False,
        index=True,
    )
    session_type: Mapped[SessionType] = mapped_column(Enum(SessionType), nullable=False)
    ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    instance_name: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True, index=True)
    evolution_status: Mapped[str | None] = mapped_column(String(32), nullable=True, default="disconnected")
    evolution_qr_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    evolution_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    evolution_profile_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evolution_webhook_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    proxy_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    proxy_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proxy_protocol: Mapped[str | None] = mapped_column(String(16), nullable=True, default="http")
    proxy_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    proxy_password_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxy_pool_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proxy_assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    account: Mapped["Account"] = relationship("Account", back_populates="channel_sessions")


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (Index("ix_import_batches_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    original_file_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_rows_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalid_rows_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_rows_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[ImportStatus] = mapped_column(
        Enum(ImportStatus),
        nullable=False,
        default=ImportStatus.PENDING,
    )
    uploaded_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    rows: Mapped[list["ImportRow"]] = relationship(
        "ImportRow",
        back_populates="batch",
        cascade="all, delete-orphan",
    )
    imported_contacts: Mapped[list["Contact"]] = relationship(
        "Contact",
        back_populates="source_import",
        foreign_keys="Contact.source_import_id",
    )


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (
        Index("ix_import_rows_batch_id_row_index", "batch_id", "row_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batches.id"),
        nullable=False,
        index=True,
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    normalized_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[ImportRowStatus] = mapped_column(
        Enum(ImportRowStatus),
        nullable=False,
        default=ImportRowStatus.PENDING,
        index=True,
    )
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate_of_contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    batch: Mapped["ImportBatch"] = relationship("ImportBatch", back_populates="rows")
    duplicate_of_contact: Mapped["Contact | None"] = relationship(
        "Contact",
        foreign_keys=[duplicate_of_contact_id],
    )
    imported_contacts: Mapped[list["Contact"]] = relationship(
        "Contact",
        back_populates="source_import_row",
        foreign_keys="Contact.source_import_row_id",
    )


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        Index("ix_contacts_campaign_id", "campaign_id"),
        Index("ix_contacts_phone", "phone"),
        UniqueConstraint("campaign_id", "phone", name="uq_contact_campaign_phone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id"),
        nullable=True,
    )
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    phone_e164: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        unique=True,
        index=True,
    )
    channel_handle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    locale: Mapped[str | None] = mapped_column(String(16), nullable=True)
    consent_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ConsentStatus.UNKNOWN.value,
    )
    blacklisted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    extra_variables: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)
    source_import_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_batches.id"),
        nullable=True,
        index=True,
    )
    source_import_row_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_rows.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    campaign: Mapped["Campaign | None"] = relationship(
        "Campaign",
        back_populates="contacts",
        foreign_keys=[campaign_id],
    )
    campaign_recipients: Mapped[list["CampaignRecipient"]] = relationship(
        "CampaignRecipient",
        back_populates="contact",
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="contact",
    )
    staged_items: Mapped[list["StagedQueueItem"]] = relationship(
        "StagedQueueItem",
        back_populates="contact",
    )
    source_import: Mapped["ImportBatch | None"] = relationship(
        "ImportBatch",
        back_populates="imported_contacts",
        foreign_keys=[source_import_id],
    )
    source_import_row: Mapped["ImportRow | None"] = relationship(
        "ImportRow",
        back_populates="imported_contacts",
        foreign_keys=[source_import_row_id],
    )
    opt_events: Mapped[list["OptEvent"]] = relationship(
        "OptEvent",
        back_populates="contact",
    )


class OptEvent(Base):
    __tablename__ = "opt_events"
    __table_args__ = (Index("ix_opt_events_contact_id", "contact_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id"),
        nullable=False,
        index=True,
    )
    opted_in: Mapped[bool] = mapped_column(Boolean, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    channel: Mapped[PlatformType | None] = mapped_column(
        Enum(PlatformType),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    contact: Mapped["Contact"] = relationship("Contact", back_populates="opt_events")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    intent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=CampaignStatus.DRAFT.value,
    )
    max_contacts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    platform: Mapped[PlatformType] = mapped_column(Enum(PlatformType), nullable=False)
    template_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    use_gpt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    include_products: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    schedule_start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    live_rate_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    contacts: Mapped[list["Contact"]] = relationship(
        "Contact",
        back_populates="campaign",
        foreign_keys="Contact.campaign_id",
    )
    recipients: Mapped[list["CampaignRecipient"]] = relationship(
        "CampaignRecipient",
        back_populates="campaign",
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="campaign",
    )
    staged_items: Mapped[list["StagedQueueItem"]] = relationship(
        "StagedQueueItem",
        back_populates="campaign",
    )


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (
        Index("ix_campaign_recipients_campaign_send_status", "campaign_id", "send_status"),
        UniqueConstraint("campaign_id", "contact_id", name="uq_campaign_recipient_campaign_contact"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id"),
        nullable=False,
        index=True,
    )
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id"),
        nullable=False,
        index=True,
    )
    render_status: Mapped[RenderStatus] = mapped_column(
        Enum(RenderStatus),
        nullable=False,
        default=RenderStatus.PENDING,
    )
    send_status: Mapped[SendStatus] = mapped_column(
        Enum(SendStatus),
        nullable=False,
        default=SendStatus.PENDING,
    )
    final_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="recipients")
    contact: Mapped["Contact"] = relationship("Contact", back_populates="campaign_recipients")
    final_message: Mapped["Message | None"] = relationship(
        "Message",
        foreign_keys=[final_message_id],
    )


class ProductSnapshot(Base):
    __tablename__ = "product_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    normalized_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="product_snapshot",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id"),
        nullable=False,
        index=True,
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id"),
        nullable=False,
        index=True,
    )
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id"),
        nullable=False,
        index=True,
    )
    rendered_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    product_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_snapshots.id"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="messages")
    account: Mapped["Account"] = relationship("Account", back_populates="messages")
    contact: Mapped["Contact"] = relationship("Contact", back_populates="messages")
    product_snapshot: Mapped["ProductSnapshot | None"] = relationship(
        "ProductSnapshot",
        back_populates="messages",
    )
    attempts: Mapped[list["MessageAttempt"]] = relationship(
        "MessageAttempt",
        back_populates="message",
    )


class MessageAttempt(Base):
    __tablename__ = "message_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id"),
        nullable=False,
        index=True,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[MessageAttemptStatus] = mapped_column(
        Enum(MessageAttemptStatus),
        nullable=False,
        default=MessageAttemptStatus.STARTED,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    platform_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    message: Mapped["Message"] = relationship("Message", back_populates="attempts")


class RatePolicy(Base):
    __tablename__ = "rate_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[PlatformType] = mapped_column(Enum(PlatformType), nullable=False)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id"),
        nullable=True,
        index=True,
    )
    per_second: Mapped[float | None] = mapped_column(Float, nullable=True)
    hourly_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quiet_hours: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    random_delay_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    random_delay_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    accounts: Mapped[list["Account"]] = relationship(
        "Account",
        back_populates="policy",
        foreign_keys="Account.policy_id",
    )


class KbDocument(Base):
    __tablename__ = "kb_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    chunk_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    visibility: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[RoleType] = mapped_column(
        Enum(RoleType),
        nullable=False,
        default=RoleType.VIEWER,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_timestamp", "timestamp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(100), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class RenderedMessage(Base):
    """پیام رندرشده برای آماده‌سازی صف — جدا از Message ارسال واقعی."""

    __tablename__ = "rendered_messages"
    __table_args__ = (Index("ix_rendered_messages_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int | None] = mapped_column(
        ForeignKey("campaigns.id"),
        nullable=True,
        index=True,
    )
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id"),
        nullable=True,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    final_text: Mapped[str] = mapped_column(Text, nullable=False)
    render_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    used_kb: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    used_products: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    product_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_snapshots.id"),
        nullable=True,
        index=True,
    )
    snapshot_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ready_for_queue: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    warnings: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    queue_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    campaign: Mapped["Campaign | None"] = relationship("Campaign")
    contact: Mapped["Contact | None"] = relationship("Contact")
    product_snapshot: Mapped["ProductSnapshot | None"] = relationship("ProductSnapshot")
    staged_items: Mapped[list["StagedQueueItem"]] = relationship(
        "StagedQueueItem",
        back_populates="rendered_message",
    )


class StagedQueueItem(Base):
    """آیتم آماده‌شده در دیتابیس — بدون push به Redis worker queue."""

    __tablename__ = "staged_queue_items"
    __table_args__ = (
        Index("ix_staged_queue_items_campaign_id", "campaign_id"),
        Index("ix_staged_queue_items_contact_id", "contact_id"),
        Index("ix_staged_queue_items_status", "status"),
        Index("ix_staged_queue_items_channel", "channel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id"),
        nullable=False,
    )
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("contacts.id"),
        nullable=False,
    )
    rendered_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("rendered_messages.id"),
        nullable=True,
        unique=True,
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=StagedQueueItemStatus.STAGED.value,
    )
    final_text: Mapped[str] = mapped_column(Text, nullable=False)
    queue_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    campaign: Mapped["Campaign"] = relationship(
        "Campaign",
        back_populates="staged_items",
    )
    contact: Mapped["Contact"] = relationship(
        "Contact",
        back_populates="staged_items",
    )
    rendered_message: Mapped["RenderedMessage | None"] = relationship(
        "RenderedMessage",
        back_populates="staged_items",
    )


class EvolutionWebhookEvent(Base):
    __tablename__ = "evolution_webhook_events"
    __table_args__ = (
        Index("ix_evolution_webhook_events_timestamp", "created_at"),
        Index("ix_evolution_webhook_events_instance", "instance_name"),
        Index("ix_evolution_webhook_events_message_id", "platform_message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    platform_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remote_jid: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("message_attempts.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
