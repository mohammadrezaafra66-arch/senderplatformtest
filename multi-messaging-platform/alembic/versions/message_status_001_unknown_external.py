"""message_status_001: add unknown external result statuses.

No historical rewrite. Old delivered/read values stay as stored.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "message_status_001"
down_revision: Union[str, Sequence[str], None] = "contact_tags_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE sendstatus ADD VALUE IF NOT EXISTS 'UNKNOWN_EXTERNAL_RESULT'"
    )
    op.execute(
        "ALTER TYPE messageattemptstatus ADD VALUE IF NOT EXISTS 'UNKNOWN_EXTERNAL'"
    )


def downgrade() -> None:
    pass
