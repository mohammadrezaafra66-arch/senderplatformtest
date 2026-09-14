"""contact_soft_delete_001: soft-delete metadata for contacts

Revision ID: contact_soft_delete_001
Revises: generic_archive_001
Create Date: 2026-09-02

Adds nullable delete metadata only. Does NOT delete existing rows.
All existing contacts remain deleted_at=NULL (active).

NOT auto-applied to production — requires operator approval.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "contact_soft_delete_001"
down_revision: Union[str, Sequence[str], None] = "generic_archive_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("deleted_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "contacts",
        sa.Column("delete_reason", sa.String(length=512), nullable=True),
    )
    op.create_index("ix_contacts_deleted_at", "contacts", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_contacts_deleted_at", table_name="contacts")
    op.drop_column("contacts", "delete_reason")
    op.drop_column("contacts", "deleted_by")
    op.drop_column("contacts", "deleted_at")
