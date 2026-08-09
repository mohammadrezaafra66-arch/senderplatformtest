"""Add selectable sender accounts to campaigns.

Revision ID: campaign_accounts_001
Revises: rubika_v2_phase8_001
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "campaign_accounts_001"
down_revision: Union[str, Sequence[str], None] = "rubika_v2_phase8_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "campaign_accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="1", nullable=False),
        sa.Column("weight", sa.Integer(), server_default="1", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("priority >= 1", name="ck_campaign_accounts_priority_gte_1"),
        sa.CheckConstraint("weight >= 1", name="ck_campaign_accounts_weight_gte_1"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "campaign_id",
            "account_id",
            name="uq_campaign_accounts_campaign_id_account_id",
        ),
    )
    op.create_index(
        "ix_campaign_accounts_campaign_id", "campaign_accounts", ["campaign_id"]
    )
    op.create_index(
        "ix_campaign_accounts_account_id", "campaign_accounts", ["account_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_campaign_accounts_account_id", table_name="campaign_accounts")
    op.drop_index("ix_campaign_accounts_campaign_id", table_name="campaign_accounts")
    op.drop_table("campaign_accounts")
