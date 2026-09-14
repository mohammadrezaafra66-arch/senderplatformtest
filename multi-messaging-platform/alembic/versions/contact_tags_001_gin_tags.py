"""contact_tags_001: GIN index for campaign tag audience queries.

contacts.tags already exists as JSON/JSONB. No data rewrite.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "contact_tags_001"
down_revision: Union[str, Sequence[str], None] = "rubika_schedule_slots_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_contacts_tags_gin ON contacts USING gin ((tags::jsonb))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_contacts_tags_gin")
