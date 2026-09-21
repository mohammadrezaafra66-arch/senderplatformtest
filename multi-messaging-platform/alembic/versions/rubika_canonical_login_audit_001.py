"""rubika_canonical_login_audit_001: snapshot ACTIVE accounts vs canonical session.

Revision ID: rubika_canonical_login_audit_001
Revises: rubika_account_activation_001
Create Date: 2026-09-16

Read-only check. Does not rewrite ChannelSession, identity, OTP, or pool.

has_canonical_session = exactly one ACTIVE rubika_session whose identity_guid
matches accounts.rubika_guid with a bound identity status.
needs_relogin = the inverse for ACTIVE Rubika accounts.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "rubika_canonical_login_audit_001"
down_revision: Union[str, Sequence[str], None] = "rubika_account_activation_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rubika_canonical_session_checks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey("accounts.id"),
            nullable=False,
        ),
        sa.Column("has_canonical_session", sa.Boolean(), nullable=False),
        sa.Column("needs_relogin", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("session_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "account_id",
            name="uq_rubika_canonical_session_checks_account_id",
        ),
    )
    op.create_index(
        "ix_rubika_canonical_session_checks_needs_relogin",
        "rubika_canonical_session_checks",
        ["needs_relogin"],
        unique=False,
    )
    op.execute(
        sa.text(
            """
            INSERT INTO rubika_canonical_session_checks (
                account_id,
                has_canonical_session,
                needs_relogin,
                reason,
                session_count,
                checked_at
            )
            SELECT
                a.id,
                CASE
                    WHEN COALESCE(stats.active_n, 0) = 1
                         AND NULLIF(BTRIM(a.rubika_guid), '') IS NOT NULL
                         AND NULLIF(BTRIM(stats.identity_guid), '') IS NOT NULL
                         AND BTRIM(a.rubika_guid) = BTRIM(stats.identity_guid)
                         AND LOWER(COALESCE(a.rubika_identity_status::text, 'unbound'))
                             NOT IN ('unbound', 'mismatch_locked')
                    THEN TRUE
                    ELSE FALSE
                END AS has_canonical_session,
                CASE
                    WHEN COALESCE(stats.active_n, 0) = 1
                         AND NULLIF(BTRIM(a.rubika_guid), '') IS NOT NULL
                         AND NULLIF(BTRIM(stats.identity_guid), '') IS NOT NULL
                         AND BTRIM(a.rubika_guid) = BTRIM(stats.identity_guid)
                         AND LOWER(COALESCE(a.rubika_identity_status::text, 'unbound'))
                             NOT IN ('unbound', 'mismatch_locked')
                    THEN FALSE
                    ELSE TRUE
                END AS needs_relogin,
                CASE
                    WHEN COALESCE(stats.active_n, 0) = 0 THEN 'NO_ACTIVE_SESSION'
                    WHEN COALESCE(stats.active_n, 0) > 1 THEN 'MULTIPLE_ACTIVE_SESSIONS'
                    WHEN COALESCE(stats.active_n, 0) = 1
                         AND NULLIF(BTRIM(a.rubika_guid), '') IS NOT NULL
                         AND NULLIF(BTRIM(stats.identity_guid), '') IS NOT NULL
                         AND BTRIM(a.rubika_guid) = BTRIM(stats.identity_guid)
                         AND LOWER(COALESCE(a.rubika_identity_status::text, 'unbound'))
                             NOT IN ('unbound', 'mismatch_locked')
                    THEN 'CANONICAL_OK'
                    ELSE 'NOT_CANONICAL_MANAGED'
                END AS reason,
                COALESCE(stats.total_n, 0) AS session_count,
                NOW() AT TIME ZONE 'utc'
            FROM accounts a
            LEFT JOIN (
                SELECT
                    account_id,
                    COUNT(*) FILTER (
                        WHERE LOWER(session_status::text) = 'active'
                    ) AS active_n,
                    COUNT(*) AS total_n,
                    MAX(identity_guid) FILTER (
                        WHERE LOWER(session_status::text) = 'active'
                    ) AS identity_guid
                FROM channel_sessions
                WHERE LOWER(session_type::text) IN ('rubika_session')
                GROUP BY account_id
            ) stats ON stats.account_id = a.id
            WHERE LOWER(a.platform::text) = 'rubika'
              AND LOWER(a.status::text) = 'active'
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rubika_canonical_session_checks_needs_relogin",
        table_name="rubika_canonical_session_checks",
    )
    op.drop_table("rubika_canonical_session_checks")
