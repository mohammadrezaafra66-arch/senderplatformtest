"""add account send settings table

Revision ID: add_send_settings_001
Revises: d745d96a635e
Create Date: 2026-06-29
"""

from alembic import op
import sqlalchemy as sa

revision = "add_send_settings_001"
down_revision = "d745d96a635e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "account_send_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "min_delay_seconds", sa.Integer(), nullable=False, server_default="45"
        ),
        sa.Column(
            "max_delay_seconds", sa.Integer(), nullable=False, server_default="90"
        ),
        sa.Column(
            "floor_delay_seconds", sa.Integer(), nullable=False, server_default="10"
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    op.drop_table("account_send_settings")
