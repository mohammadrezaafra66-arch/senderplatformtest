"""Add worker delivery statuses to sendstatus and messageattemptstatus enums."""

from typing import Sequence, Union

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEND_STATUS_VALUES = (
    "DRY_RUN",
    "SHADOW_SENT",
    "OPTED_OUT",
    "BLACKLISTED",
)

_MESSAGE_ATTEMPT_STATUS_VALUES = (
    "DRY_RUN",
    "SHADOW_SENT",
)


def upgrade() -> None:
    for value in _SEND_STATUS_VALUES:
        op.execute(f"ALTER TYPE sendstatus ADD VALUE IF NOT EXISTS '{value}'")
    for value in _MESSAGE_ATTEMPT_STATUS_VALUES:
        op.execute(
            f"ALTER TYPE messageattemptstatus ADD VALUE IF NOT EXISTS '{value}'"
        )


def downgrade() -> None:
    # PostgreSQL cannot drop individual enum values safely; leave types unchanged.
    pass
