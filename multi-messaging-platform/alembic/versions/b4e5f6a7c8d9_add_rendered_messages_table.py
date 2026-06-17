"""add rendered_messages table

Revision ID: b4e5f6a7c8d9
Revises: a3c7d9e1f2b4
Create Date: 2026-06-15 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b4e5f6a7c8d9"
down_revision: Union[str, Sequence[str], None] = "a3c7d9e1f2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rendered_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=True),
        sa.Column("contact_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("final_text", sa.Text(), nullable=False),
        sa.Column("render_mode", sa.String(length=32), nullable=False),
        sa.Column("used_kb", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("used_products", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("product_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("snapshot_expires_at", sa.DateTime(), nullable=True),
        sa.Column("ready_for_queue", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("queue_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.ForeignKeyConstraint(["product_snapshot_id"], ["product_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rendered_messages_campaign_id",
        "rendered_messages",
        ["campaign_id"],
        unique=False,
    )
    op.create_index(
        "ix_rendered_messages_contact_id",
        "rendered_messages",
        ["contact_id"],
        unique=False,
    )
    op.create_index(
        "ix_rendered_messages_created_at",
        "rendered_messages",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_rendered_messages_product_snapshot_id",
        "rendered_messages",
        ["product_snapshot_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_rendered_messages_product_snapshot_id", table_name="rendered_messages")
    op.drop_index("ix_rendered_messages_created_at", table_name="rendered_messages")
    op.drop_index("ix_rendered_messages_contact_id", table_name="rendered_messages")
    op.drop_index("ix_rendered_messages_campaign_id", table_name="rendered_messages")
    op.drop_table("rendered_messages")
