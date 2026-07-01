"""rubika v2 phase 8: content schedule table for status publishing

Revision ID: rubika_v2_phase8_001
Revises: rubika_v2_phase1_001
Create Date: 2026-07-01

این جدول برای انتشار خودکار استاتوس (Rubino) از زمان‌بندی محتوا استفاده می‌شود —
نیازمندی ۲۴ سند (انتشار خودکار استاتوس). اکانت status باید کاملاً مجزا از
اکانت‌های ارسال/پایش باشد (قانون امنیتی بخش هفت سند).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "rubika_v2_phase8_001"
down_revision: Union[str, Sequence[str], None] = "rubika_v2_phase1_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rubika_content_schedules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("media_path", sa.String(length=1024), nullable=True),
        # نوع محتوا: "Picture" | "Video" | "text_only"
        sa.Column("content_type", sa.String(length=32), nullable=False, server_default="Picture"),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
        sa.Column("published", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rubika_content_schedules_scheduled_at",
        "rubika_content_schedules",
        ["scheduled_at", "published"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_rubika_content_schedules_scheduled_at",
                  table_name="rubika_content_schedules")
    op.drop_table("rubika_content_schedules")
