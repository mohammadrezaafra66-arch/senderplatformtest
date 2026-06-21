"""Add opt_events table for opt-in/opt-out tracking."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d6e7f8a9b0c1"
down_revision: Union[str, Sequence[str], None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

platform_type_enum = postgresql.ENUM(
    "whatsapp",
    "telegram",
    "rubika",
    "bale",
    name="platformtype",
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "opt_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("opted_in", sa.Boolean(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("channel", platform_type_enum, nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_opt_events_contact_id", "opt_events", ["contact_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_opt_events_contact_id", table_name="opt_events")
    op.drop_table("opt_events")
