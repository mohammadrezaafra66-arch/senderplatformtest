"""Add BALE_SESSION to sessiontype enum and add aiobale session support."""
from typing import Sequence, Union
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "9f8e7d6c5b4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # اضافه کردن BALE_SESSION به enum sessiontype در PostgreSQL
    op.execute("ALTER TYPE sessiontype ADD VALUE IF NOT EXISTS 'BALE_SESSION'")
    op.execute("ALTER TYPE sessiontype ADD VALUE IF NOT EXISTS 'bale_session'")


def downgrade() -> None:
    # PostgreSQL از حذف enum value پشتیبانی نمی‌کند
    pass
