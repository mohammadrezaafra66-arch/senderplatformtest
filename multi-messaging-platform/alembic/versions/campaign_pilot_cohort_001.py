"""campaign_pilot_cohort_001: frozen pilot recipient cohort.

Revision ID: campaign_pilot_cohort_001
Revises: campaign_send_guards_001

Not applied to the live readiness database by this change.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "campaign_pilot_cohort_001"
down_revision: Union[str, Sequence[str], None] = "campaign_send_guards_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "campaign_pilot_states",
        sa.Column("recipient_limit", sa.Integer(), nullable=True),
    )
    op.create_table(
        "campaign_pilot_cohort_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column(
            "campaign_recipient_id",
            sa.Integer(),
            sa.ForeignKey("campaign_recipients.id"),
            nullable=False,
        ),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "campaign_id",
            "campaign_recipient_id",
            name="uq_pilot_cohort_campaign_recipient",
        ),
        sa.UniqueConstraint(
            "campaign_id",
            "ordinal",
            name="uq_pilot_cohort_campaign_ordinal",
        ),
        sa.UniqueConstraint(
            "campaign_id",
            "contact_id",
            name="uq_pilot_cohort_campaign_contact",
        ),
    )


def downgrade() -> None:
    op.drop_table("campaign_pilot_cohort_members")
    op.drop_column("campaign_pilot_states", "recipient_limit")
