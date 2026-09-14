"""generic_archive_001: soft archive columns for accounts + campaigns

Revision ID: generic_archive_001
Revises: rubika_l3_login_challenge_001
Create Date: 2026-09-02

Adds nullable archive metadata only. Does NOT archive existing rows.
All existing entities remain archived_at=NULL (active).

NOT auto-applied to production — requires operator approval (Phase 20).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "generic_archive_001"
down_revision: Union[str, Sequence[str], None] = "rubika_l3_login_challenge_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("archived_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("archive_reason", sa.String(length=512), nullable=True),
    )
    op.create_index("ix_accounts_archived_at", "accounts", ["archived_at"])

    op.add_column(
        "campaigns",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column("archived_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column("archive_reason", sa.String(length=512), nullable=True),
    )
    op.create_index("ix_campaigns_archived_at", "campaigns", ["archived_at"])


def downgrade() -> None:
    op.drop_index("ix_campaigns_archived_at", table_name="campaigns")
    op.drop_column("campaigns", "archive_reason")
    op.drop_column("campaigns", "archived_by")
    op.drop_column("campaigns", "archived_at")

    op.drop_index("ix_accounts_archived_at", table_name="accounts")
    op.drop_column("accounts", "archive_reason")
    op.drop_column("accounts", "archived_by")
    op.drop_column("accounts", "archived_at")
