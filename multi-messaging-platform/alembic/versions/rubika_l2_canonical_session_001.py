"""rubika L2: canonical session_status + account identity binding

Revision ID: rubika_l2_canonical_session_001
Revises: campaign_accounts_001
Create Date: 2026-08-29

SAFE STAGING:
1) Add columns (session_status defaults to legacy_unclassified).
2) Backfill ALL existing channel_sessions.session_status -> legacy_unclassified
   (never infer ACTIVE from max(id) / account.status).
3) Create partial unique index for one ACTIVE rubika_session per account.
   After backfill, zero ACTIVE rows exist, so the index is safe.

DOES NOT:
- promote Account12/79 (or any account) to ACTIVE
- delete historical rows
- enforce global accounts.rubika_guid uniqueness (deferred until duplicate inventory)

PRODUCTION APPLY requires separate operator approval. This file is code-only until then.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "rubika_l2_canonical_session_001"
down_revision: Union[str, Sequence[str], None] = "campaign_accounts_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SESSION_STATUS = sa.Enum(
    "legacy_unclassified",
    "pending_login",
    "validating",
    "active",
    "superseded",
    "invalid",
    "decrypt_failed",
    "revoked",
    name="rubikasessionstatus",
    create_type=True,
)

_IDENTITY_STATUS = sa.Enum(
    "unbound",
    "verified",
    "mismatch_locked",
    "operator_override",
    name="rubikaidentitystatus",
    create_type=True,
)


def _assert_no_active_duplicates(conn) -> None:
    """Refuse index creation if ACTIVE duplicates already exist (should not after backfill)."""
    rows = conn.execute(
        sa.text(
            """
            SELECT account_id, COUNT(*) AS n
            FROM channel_sessions
            WHERE session_status = 'active'
              AND (
                    session_type::text IN ('rubika_session', 'RUBIKA_SESSION')
                  )
            GROUP BY account_id
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    if rows:
        ids = ", ".join(str(r[0]) for r in rows)
        raise RuntimeError(
            "PRECONDITION_FAILED: cannot create uq_channel_sessions_one_active_rubika; "
            f"accounts with multiple ACTIVE rubika sessions: {ids}. "
            "Resolve via operator-approved L16 canonicalization — do not auto-pick."
        )


def _assert_legacy_duplicates_not_auto_promoted(conn) -> None:
    """Guard: migration must not leave ACTIVE rows created by inference."""
    active = conn.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM channel_sessions
            WHERE session_status = 'active'
              AND session_type::text IN ('rubika_session', 'RUBIKA_SESSION')
            """
        )
    ).scalar()
    if int(active or 0) > 0:
        raise RuntimeError(
            "PRECONDITION_FAILED: L2 upgrade refuses non-zero ACTIVE rubika sessions "
            "immediately after legacy backfill. ACTIVE must only be set by explicit "
            "operator-approved promotion (L16), never by this migration."
        )


def upgrade() -> None:
    bind = op.get_bind()

    _SESSION_STATUS.create(bind, checkfirst=True)
    _IDENTITY_STATUS.create(bind, checkfirst=True)

    op.add_column(
        "channel_sessions",
        sa.Column(
            "session_status",
            _SESSION_STATUS,
            nullable=True,
            server_default="legacy_unclassified",
        ),
    )
    op.add_column(
        "channel_sessions",
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "channel_sessions",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "channel_sessions",
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "channel_sessions",
        sa.Column("validation_error_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "channel_sessions",
        sa.Column("identity_guid", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "channel_sessions",
        sa.Column("login_attempt_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_channel_sessions_session_status",
        "channel_sessions",
        ["session_status"],
        unique=False,
    )

    op.add_column(
        "accounts",
        sa.Column("rubika_guid", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column("rubika_identity_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "accounts",
        sa.Column(
            "rubika_identity_status",
            _IDENTITY_STATUS,
            nullable=True,
            server_default="unbound",
        ),
    )
    op.create_index("ix_accounts_rubika_guid", "accounts", ["rubika_guid"], unique=False)

    # Explicit backfill — never infer ACTIVE.
    op.execute(
        sa.text(
            """
            UPDATE channel_sessions
            SET session_status = 'legacy_unclassified'
            WHERE session_status IS NULL
               OR session_status::text <> 'legacy_unclassified'
            """
        )
    )
    # Force every row to LEGACY regardless of any accidental default misuse.
    op.execute(
        sa.text(
            """
            UPDATE channel_sessions
            SET session_status = 'legacy_unclassified'
            """
        )
    )

    _assert_legacy_duplicates_not_auto_promoted(bind)
    _assert_no_active_duplicates(bind)

    # Partial unique: at most one ACTIVE rubika session per account.
    # Use native enum label (RUBIKA_SESSION) — no ::text cast (not IMMUTABLE in PG index preds).
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_channel_sessions_one_active_rubika
            ON channel_sessions (account_id)
            WHERE session_status = 'active'
              AND session_type = 'RUBIKA_SESSION'
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_channel_sessions_one_active_rubika"))
    op.drop_index("ix_accounts_rubika_guid", table_name="accounts")
    op.drop_column("accounts", "rubika_identity_status")
    op.drop_column("accounts", "rubika_identity_verified_at")
    op.drop_column("accounts", "rubika_guid")
    op.drop_index("ix_channel_sessions_session_status", table_name="channel_sessions")
    op.drop_column("channel_sessions", "login_attempt_id")
    op.drop_column("channel_sessions", "identity_guid")
    op.drop_column("channel_sessions", "validation_error_code")
    op.drop_column("channel_sessions", "invalidated_at")
    op.drop_column("channel_sessions", "superseded_at")
    op.drop_column("channel_sessions", "validated_at")
    op.drop_column("channel_sessions", "session_status")
    _IDENTITY_STATUS.drop(op.get_bind(), checkfirst=True)
    _SESSION_STATUS.drop(op.get_bind(), checkfirst=True)
