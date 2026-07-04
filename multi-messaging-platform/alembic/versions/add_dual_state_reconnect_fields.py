"""add dual-state and reconnect fields to channel_sessions

Revision ID: add_dual_state_001
Revises: add_failure_reason_001
Create Date: 2026-07-04
"""

from alembic import op
import sqlalchemy as sa

revision = "add_dual_state_001"
down_revision = "add_failure_reason_001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "channel_sessions",
        sa.Column(
            "authorization_state",
            sa.String(32),
            nullable=True,
            server_default="not_authorized",
        ),
    )
    op.add_column(
        "channel_sessions",
        sa.Column(
            "socket_state",
            sa.String(16),
            nullable=True,
            server_default="offline",
        ),
    )
    op.add_column(
        "channel_sessions",
        sa.Column(
            "reconnect_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "channel_sessions",
        sa.Column("last_disconnect_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "channel_sessions",
        sa.Column("disconnect_events", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("channel_sessions", "disconnect_events")
    op.drop_column("channel_sessions", "last_disconnect_at")
    op.drop_column("channel_sessions", "reconnect_attempts")
    op.drop_column("channel_sessions", "socket_state")
    op.drop_column("channel_sessions", "authorization_state")
