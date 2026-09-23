"""campaign_send_guards_001: success ledger, pilot permits, shared product snapshot.

Revision ID: campaign_send_guards_001
Revises: account_message_limits_001

Not applied to the live readiness database by this change.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "campaign_send_guards_001"
down_revision: Union[str, Sequence[str], None] = "account_message_limits_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campaign_send_successes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id"), nullable=False),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id"), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("campaign_id", "contact_id", name="uq_campaign_send_success_recipient"),
        sa.UniqueConstraint("idempotency_key", name="uq_campaign_send_success_idempotency"),
    )
    op.create_table(
        "campaign_pilot_states",
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("success_limit", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("reserved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("confirmed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("auto_pause", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("admin_resume_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "product_send_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("product_send_snapshots")
    op.drop_table("campaign_pilot_states")
    op.drop_table("campaign_send_successes")
