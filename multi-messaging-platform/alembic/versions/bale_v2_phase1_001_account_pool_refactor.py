"""bale account pool refactor — phase-based, Account.status as source of truth

Revision ID: bale_v2_phase1_001
Revises: 9c75a77b45cc
Create Date: 2026-07-29
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "bale_v2_phase1_001"
down_revision: Union[str, None] = "add_failure_reason_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # bale_account_pool — ایجاد جدول با ساختار جدید (phase-based)
    op.create_table(
        "bale_account_pool",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False, index=True),
        sa.Column("phase", sa.String(16), nullable=False, server_default="day"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_error_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_message", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("account_id", "phase", name="uq_bale_pool_account_phase"),
    )
    op.create_index("ix_bale_pool_phase_priority", "bale_account_pool", ["phase", "priority"])

    # bale_sender_schedules — ایجاد جدول با ساختار جدید
    op.create_table(
        "bale_sender_schedules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("phase", sa.String(16), nullable=False, unique=True, server_default="day"),
        sa.Column("start_hour", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("end_hour", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("max_per_hour", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    # seed یک ردیف پیش‌فرض
    op.execute("""
        INSERT INTO bale_sender_schedules (phase, start_hour, end_hour, max_per_hour, is_active, created_at, updated_at)
        VALUES ('day', 8, 22, 50, true, NOW(), NOW())
    """)


def downgrade() -> None:
    op.drop_index("ix_bale_pool_phase_priority", table_name="bale_account_pool")
    op.drop_table("bale_account_pool")
    op.drop_table("bale_sender_schedules")
