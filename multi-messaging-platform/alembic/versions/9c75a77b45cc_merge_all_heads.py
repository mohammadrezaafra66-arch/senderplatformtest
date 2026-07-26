"""merge all heads

Revision ID: 9c75a77b45cc
Revises: a1b2c3d4e5f6, add_warming_started_001
Create Date: 2026-07-26 07:40:48.215668

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c75a77b45cc'
down_revision: Union[str, Sequence[str], None] = ('a1b2c3d4e5f6', 'add_warming_started_001')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
