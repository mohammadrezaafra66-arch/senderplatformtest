"""Add Bale user account tracking tables."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9f8e7d6c5b4a"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bale_account_pool",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("warm_up_started_at", sa.DateTime(), nullable=True),
        sa.Column("is_warmed_up", sa.Boolean(), nullable=True),
        sa.Column("daily_cap_today", sa.Integer(), nullable=True),
        sa.Column("sent_today", sa.Integer(), nullable=True),
        sa.Column("last_count_reset_date", sa.DateTime(), nullable=True),
        sa.Column("is_healthy", sa.Boolean(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id"),
    )
    op.create_table(
        "bale_global_sent_registry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("phone_number", sa.String(length=20), nullable=False),
        sa.Column("first_sent_at", sa.DateTime(), nullable=True),
        sa.Column("send_count", sa.Integer(), nullable=True),
        sa.Column("last_sent_campaign_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["last_sent_campaign_id"], ["campaigns.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone_number"),
    )
    op.create_index(
        op.f("ix_bale_global_sent_registry_phone_number"),
        "bale_global_sent_registry",
        ["phone_number"],
        unique=True,
    )
    op.create_table(
        "bale_sender_schedules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("start_hour", sa.Integer(), nullable=True),
        sa.Column("end_hour", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_bale_global_sent_registry_phone_number"),
        table_name="bale_global_sent_registry",
    )
    op.drop_table("bale_sender_schedules")
    op.drop_table("bale_global_sent_registry")
    op.drop_table("bale_account_pool")
