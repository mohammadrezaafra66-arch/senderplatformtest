"""add warming_started_at to accounts (Phase 4 warming)

Revision ID: add_warming_started_001
Revises: add_dual_state_001
Create Date: 2026-07-05

NOTE: Column only. No automatic backfill — existing accounts get NULL, which the
warming logic treats as "starting today" (day 0). Any historical backfill is a
deliberate, manual decision made later.
"""

from alembic import op
import sqlalchemy as sa

revision = "add_warming_started_001"
down_revision = "add_dual_state_001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "accounts",
        sa.Column("warming_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("accounts", "warming_started_at")
