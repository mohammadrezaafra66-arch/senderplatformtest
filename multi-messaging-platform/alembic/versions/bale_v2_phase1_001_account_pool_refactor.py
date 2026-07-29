"""bale account pool refactor — phase-based, Account.status as source of truth

Revision ID: bale_v2_phase1_001
Revises: 9c75a77b45cc
Create Date: 2026-07-29
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "bale_v2_phase1_001"
down_revision: Union[str, None] = "9c75a77b45cc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # bale_account_pool — حذف ستون‌های قدیمی، اضافه phase و priority
    op.drop_column("bale_account_pool", "is_healthy")
    op.drop_column("bale_account_pool", "is_warmed_up")
    op.drop_column("bale_account_pool", "warm_up_started_at")
    op.drop_column("bale_account_pool", "daily_cap_today")
    op.drop_column("bale_account_pool", "sent_today")
    op.drop_column("bale_account_pool", "last_count_reset_date")

    op.add_column("bale_account_pool", sa.Column("phase", sa.String(16), nullable=False, server_default="day"))
    op.add_column("bale_account_pool", sa.Column("priority", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("bale_account_pool", sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))

    # unique constraint جدید روی (account_id, phase) به جای unique روی account_id تنها
    op.drop_constraint("bale_account_pool_account_id_key", "bale_account_pool", type_="unique")
    op.create_unique_constraint("uq_bale_pool_account_phase", "bale_account_pool", ["account_id", "phase"])
    op.create_index("ix_bale_pool_phase_priority", "bale_account_pool", ["phase", "priority"])

    # last_error_message — کوتاه‌تر کن (از Text به String(512))
    op.alter_column("bale_account_pool", "last_error_message", type_=sa.String(512), existing_nullable=True)

    # bale_sender_schedules — اضافه کردن phase، max_per_hour، updated_at
    op.add_column("bale_sender_schedules", sa.Column("phase", sa.String(16), nullable=False, server_default="day"))
    op.add_column("bale_sender_schedules", sa.Column("max_per_hour", sa.Integer(), nullable=False, server_default="50"))
    op.add_column("bale_sender_schedules", sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_unique_constraint("uq_bale_sender_schedules_phase", "bale_sender_schedules", ["phase"])

    # seed یک ردیف پیش‌فرض اگر جدول خالی است
    op.execute("""
        INSERT INTO bale_sender_schedules (phase, start_hour, end_hour, max_per_hour, is_active, created_at, updated_at)
        SELECT 'day', 8, 22, 50, true, NOW(), NOW()
        WHERE NOT EXISTS (SELECT 1 FROM bale_sender_schedules)
    """)


def downgrade() -> None:
    op.drop_index("ix_bale_pool_phase_priority", table_name="bale_account_pool")
    op.drop_constraint("uq_bale_pool_account_phase", "bale_account_pool", type_="unique")
    op.drop_column("bale_account_pool", "phase")
    op.drop_column("bale_account_pool", "priority")
    op.drop_column("bale_account_pool", "updated_at")

    op.add_column("bale_account_pool", sa.Column("is_healthy", sa.Boolean(), server_default="true", nullable=False))
    op.add_column("bale_account_pool", sa.Column("is_warmed_up", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("bale_account_pool", sa.Column("warm_up_started_at", sa.DateTime(), nullable=True))
    op.add_column("bale_account_pool", sa.Column("daily_cap_today", sa.Integer(), server_default="10", nullable=False))
    op.add_column("bale_account_pool", sa.Column("sent_today", sa.Integer(), server_default="0", nullable=False))
    op.add_column("bale_account_pool", sa.Column("last_count_reset_date", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_unique_constraint("bale_account_pool_account_id_key", "bale_account_pool", ["account_id"])

    op.drop_constraint("uq_bale_sender_schedules_phase", "bale_sender_schedules", type_="unique")
    op.drop_column("bale_sender_schedules", "phase")
    op.drop_column("bale_sender_schedules", "max_per_hour")
    op.drop_column("bale_sender_schedules", "updated_at")
