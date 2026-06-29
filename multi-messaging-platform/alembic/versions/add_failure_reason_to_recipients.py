"""add failure_reason to campaign_recipients

Revision ID: add_failure_reason_001
Revises: add_send_settings_001
Create Date: 2026-06-29
"""

from alembic import op
import sqlalchemy as sa

revision = "add_failure_reason_001"
down_revision = "add_send_settings_001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "campaign_recipients",
        sa.Column("failure_reason", sa.String(512), nullable=True),
    )


def downgrade():
    op.drop_column("campaign_recipients", "failure_reason")
