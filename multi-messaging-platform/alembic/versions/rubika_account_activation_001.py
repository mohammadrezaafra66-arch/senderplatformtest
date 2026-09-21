"""rubika_account_activation_001: manager confirm before send pool.

Revision ID: rubika_account_activation_001
Revises: message_status_001
Create Date: 2026-09-16

One activation row per Rubika account. Login success stays on
AccountStatus.active. Campaign send waits until state is confirmed /
ready_to_send.

Enum ownership (same pattern as rubika_l3_login_challenge_001):
  Explicit CREATE TYPE once via postgresql.ENUM(...).create(checkfirst=True).
  Table column references the type with create_type=False so create_table
  does NOT emit a second CREATE TYPE.

DOES NOT:
- change canonical OTP / prover / session promotion
- rewrite existing accounts
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "rubika_account_activation_001"
down_revision: Union[str, Sequence[str], None] = "message_status_001"
branch_labels = None
depends_on = None

_STATE_LABELS = (
    "pending",
    "challenge_sent",
    "confirmed",
    "ready_to_send",
    "failed",
)

_STATE_TYPE_NAME = "rubikaaccountactivationstate"

_STATE = postgresql.ENUM(
    *_STATE_LABELS,
    name=_STATE_TYPE_NAME,
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*_STATE_LABELS, name=_STATE_TYPE_NAME).create(bind, checkfirst=True)
    op.create_table(
        "rubika_account_activations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column(
            "state",
            _STATE,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("manager_phone", sa.String(length=32), nullable=True),
        sa.Column("challenge_message_id", sa.String(length=128), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=True),
        sa.Column("confirm_code", sa.String(length=16), nullable=True),
        sa.Column("login_challenge_id", sa.String(length=64), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_by", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.String(length=256), nullable=True),
        sa.UniqueConstraint(
            "account_id",
            name="uq_rubika_account_activations_account_id",
        ),
    )
    op.create_index(
        "ix_rubika_account_activations_account_id",
        "rubika_account_activations",
        ["account_id"],
        unique=True,
    )
    op.create_index(
        "ix_rubika_account_activations_state",
        "rubika_account_activations",
        ["state"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rubika_account_activations_state",
        table_name="rubika_account_activations",
    )
    op.drop_index(
        "ix_rubika_account_activations_account_id",
        table_name="rubika_account_activations",
    )
    op.drop_table("rubika_account_activations")
    postgresql.ENUM(*_STATE_LABELS, name=_STATE_TYPE_NAME).drop(
        op.get_bind(), checkfirst=True
    )
