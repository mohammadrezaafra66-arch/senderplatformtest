"""add expires_at to product_snapshots

Revision ID: a3c7d9e1f2b4
Revises: f8a2b1c3d4e5
Create Date: 2026-06-15 13:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a3c7d9e1f2b4"
down_revision: Union[str, Sequence[str], None] = "f8a2b1c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "product_snapshots",
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )
    op.execute(
        """
        UPDATE product_snapshots
        SET expires_at = COALESCE(fetched_at, created_at) + interval '300 seconds'
        WHERE expires_at IS NULL
        """
    )
    op.alter_column("product_snapshots", "expires_at", nullable=False)


def downgrade() -> None:
    op.drop_column("product_snapshots", "expires_at")
