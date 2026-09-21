"""account_message_limits_001: optional per-account hourly/daily send caps.

Revision ID: account_message_limits_001
Revises: rubika_canonical_login_audit_001
Create Date: 2026-09-20

NULL = unlimited for that dimension. Positive integer = exact cap.
No backfill: existing rows stay NULL/NULL.

DOES NOT:
- rewrite existing account rows
- change Redis key layout or TTL
- apply lifecycle 2/20 or 5/60 as count caps
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "account_message_limits_001"
down_revision: Union[str, Sequence[str], None] = "rubika_canonical_login_audit_001"
branch_labels = None
depends_on = None

_HOURLY_CHECK = "ck_accounts_hourly_message_limit_positive"
_DAILY_CHECK = "ck_accounts_daily_message_limit_positive"


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("hourly_message_limit", sa.Integer(), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("daily_message_limit", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        _HOURLY_CHECK,
        "accounts",
        "hourly_message_limit IS NULL OR hourly_message_limit > 0",
    )
    op.create_check_constraint(
        _DAILY_CHECK,
        "accounts",
        "daily_message_limit IS NULL OR daily_message_limit > 0",
    )


def downgrade() -> None:
    op.drop_constraint(_HOURLY_CHECK, "accounts", type_="check")
    op.drop_constraint(_DAILY_CHECK, "accounts", type_="check")
    op.drop_column("accounts", "hourly_message_limit")
    op.drop_column("accounts", "daily_message_limit")
