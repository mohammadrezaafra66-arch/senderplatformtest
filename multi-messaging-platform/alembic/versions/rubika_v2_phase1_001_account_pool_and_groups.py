"""rubika v2 phase 1: account pool, dedup registry, group monitoring, schedule

Revision ID: rubika_v2_phase1_001
Revises: add_failure_reason_001
Create Date: 2026-06-30

نکته مهم در مورد enum sessiontype:
مقدار EVOLUTION_INSTANCE قبلاً به کلاس پایتون SessionType اضافه شده بود اما
هیچ‌وقت با ALTER TYPE به enum واقعی Postgres اضافه نشد (می‌توانید با
`SELECT enum_range(NULL::sessiontype)` بررسی کنید). این migration آن اشتباه
را برای RUBIKA_SESSION تکرار نمی‌کند و هر دو مقدار را اضافه می‌کند تا
دیتابیس با models.py همگام شود.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "rubika_v2_phase1_001"
down_revision: Union[str, Sequence[str], None] = "add_failure_reason_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── همگام‌سازی enum sessiontype با models.py (رفع gap قدیمی + مقدار جدید) ───
    op.execute("ALTER TYPE sessiontype ADD VALUE IF NOT EXISTS 'EVOLUTION_INSTANCE'")
    op.execute("ALTER TYPE sessiontype ADD VALUE IF NOT EXISTS 'RUBIKA_SESSION'")

    # ─── rubika_account_pool ───
    op.create_table(
        "rubika_account_pool",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False, server_default="day"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_error_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_message", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "phase", name="uq_rubika_pool_account_phase"),
    )
    op.create_index(
        "ix_rubika_account_pool_account_id", "rubika_account_pool", ["account_id"], unique=False
    )
    op.create_index(
        "ix_rubika_pool_phase_priority", "rubika_account_pool", ["phase", "priority"], unique=False
    )

    # ─── rubika_global_sent_registry ───
    op.create_table(
        "rubika_global_sent_registry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("first_sent_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_sent_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("send_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_sent_campaign_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.ForeignKeyConstraint(
            ["last_sent_campaign_id"], ["campaigns.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rubika_global_sent_registry_contact_id",
        "rubika_global_sent_registry",
        ["contact_id"],
        unique=True,
    )

    # ─── rubika_allowed_groups ───
    op.create_table(
        "rubika_allowed_groups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("group_guid", sa.String(length=255), nullable=False),
        sa.Column("group_name", sa.String(length=512), nullable=True),
        sa.Column("listener_account_id", sa.Integer(), nullable=True),
        sa.Column("keywords", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("keyword_response", sa.Text(), nullable=True),
        sa.Column("red_keywords", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "conversation_mode_enabled", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["listener_account_id"], ["accounts.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_guid", name="uq_rubika_allowed_groups_guid"),
    )

    # ─── rubika_group_messages ───
    op.create_table(
        "rubika_group_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("group_guid", sa.String(length=255), nullable=False),
        sa.Column("group_name", sa.String(length=512), nullable=True),
        sa.Column("sender_name", sa.String(length=255), nullable=True),
        sa.Column("sender_phone", sa.String(length=32), nullable=True),
        sa.Column("message_type", sa.String(length=32), nullable=False, server_default="text"),
        sa.Column("message_text", sa.Text(), nullable=True),
        sa.Column("voice_file_path", sa.String(length=1024), nullable=True),
        sa.Column("image_file_path", sa.String(length=1024), nullable=True),
        sa.Column("transcription", sa.Text(), nullable=True),
        sa.Column("image_extracted_text", sa.Text(), nullable=True),
        sa.Column(
            "is_reply_to_our_message", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("has_red_keyword", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("ai_analyzed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("received_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["group_guid"], ["rubika_allowed_groups.group_guid"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rubika_group_messages_group_guid",
        "rubika_group_messages",
        ["group_guid"],
        unique=False,
    )
    op.create_index(
        "ix_rubika_group_messages_group_received",
        "rubika_group_messages",
        ["group_guid", "received_at"],
        unique=False,
    )

    # ─── rubika_sender_schedules ───
    op.create_table(
        "rubika_sender_schedules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False, server_default="day"),
        sa.Column("start_hour", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("end_hour", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("max_per_hour", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phase", name="uq_rubika_sender_schedules_phase"),
    )

    # پیش‌فرض دو فاز روز/شب — هماهنگ با RUBIKA_DAY_PHASE_START_HOUR/END_HOUR در workers/config.py
    op.execute(
        """
        INSERT INTO rubika_sender_schedules (phase, start_hour, end_hour, max_per_hour, is_active)
        VALUES
            ('day', 8, 22, 50, true),
            ('night', 22, 8, 50, true)
        """
    )


def downgrade() -> None:
    op.drop_table("rubika_sender_schedules")

    op.drop_index("ix_rubika_group_messages_group_received", table_name="rubika_group_messages")
    op.drop_index("ix_rubika_group_messages_group_guid", table_name="rubika_group_messages")
    op.drop_table("rubika_group_messages")

    op.drop_table("rubika_allowed_groups")
    op.drop_table("rubika_global_sent_registry")

    op.drop_index("ix_rubika_pool_phase_priority", table_name="rubika_account_pool")
    op.drop_index("ix_rubika_account_pool_account_id", table_name="rubika_account_pool")
    op.drop_table("rubika_account_pool")

    # توجه: Postgres اجازه حذف مقدار از enum را نمی‌دهد. برگرداندن enum sessiontype
    # به حالت قبل نیازمند ساخت دوباره type و migrate کردن ستون session_type است —
    # عمداً اینجا انجام نشده چون پرخطر و خارج از scope این downgrade است.
