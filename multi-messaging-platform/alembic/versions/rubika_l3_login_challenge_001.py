"""rubika L3: login challenge table for OTP state machine

Revision ID: rubika_l3_login_challenge_001
Revises: rubika_l2_canonical_session_001
Create Date: 2026-08-29

NOT applied to production in L3. Isolated tests use create_all.

Enum ownership (L4 fix):
  Explicit CREATE TYPE once via postgresql.ENUM(...).create(checkfirst=True).
  Table column references the type with create_type=False so create_table
  does NOT emit a second CREATE TYPE (avoids DuplicateObject on PG).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "rubika_l3_login_challenge_001"
down_revision: Union[str, Sequence[str], None] = "rubika_l2_canonical_session_001"
branch_labels = None
depends_on = None

_STATE_LABELS = (
    "login_not_started",
    "otp_requested",
    "otp_waiting_for_operator",
    "otp_submitted",
    "authenticating",
    "identity_verifying",
    "session_persisting",
    "ready",
    "login_failed",
    "login_expired",
    "manual_review_required",
)

_STATE_TYPE_NAME = "rubikaloginchallengestate"

# Column-bound Enum: never emits CREATE TYPE (owned by explicit create below).
_STATE = postgresql.ENUM(
    *_STATE_LABELS,
    name=_STATE_TYPE_NAME,
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    # Single CREATE TYPE path for this migration.
    postgresql.ENUM(*_STATE_LABELS, name=_STATE_TYPE_NAME).create(bind, checkfirst=True)
    op.create_table(
        "rubika_login_challenges",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("state", _STATE, nullable=False),
        sa.Column("provider_challenge_id", sa.String(length=128), nullable=True),
        sa.Column("phone_e164", sa.String(length=32), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_submit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("candidate_session_id", sa.Integer(), nullable=True),
        sa.Column("completed_session_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_rubika_login_challenges_account_id",
        "rubika_login_challenges",
        ["account_id"],
    )
    op.create_index(
        "ix_rubika_login_challenges_account_state",
        "rubika_login_challenges",
        ["account_id", "state"],
    )
    op.create_index(
        "ix_rubika_login_challenges_state",
        "rubika_login_challenges",
        ["state"],
    )


def downgrade() -> None:
    op.drop_index("ix_rubika_login_challenges_state", table_name="rubika_login_challenges")
    op.drop_index("ix_rubika_login_challenges_account_state", table_name="rubika_login_challenges")
    op.drop_index("ix_rubika_login_challenges_account_id", table_name="rubika_login_challenges")
    op.drop_table("rubika_login_challenges")
    # Drop enum only after table (no remaining references); checkfirst-safe.
    postgresql.ENUM(*_STATE_LABELS, name=_STATE_TYPE_NAME).drop(
        op.get_bind(), checkfirst=True
    )
