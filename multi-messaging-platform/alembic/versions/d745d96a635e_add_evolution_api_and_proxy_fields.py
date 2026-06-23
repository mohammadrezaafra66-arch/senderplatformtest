"""add evolution api and proxy fields

Revision ID: d745d96a635e
Revises: f1a2b3c4d5e6
Create Date: 2026-06-23
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d745d96a635e"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ─── فیلدهای evolution_* برای جدول channel_sessions ───
    op.add_column("channel_sessions", sa.Column("instance_name", sa.String(255), nullable=True))
    op.add_column("channel_sessions", sa.Column("evolution_status", sa.String(32), nullable=True, server_default="disconnected"))
    op.add_column("channel_sessions", sa.Column("evolution_qr_code", sa.Text(), nullable=True))
    op.add_column("channel_sessions", sa.Column("evolution_phone", sa.String(32), nullable=True))
    op.add_column("channel_sessions", sa.Column("evolution_profile_name", sa.String(255), nullable=True))
    op.add_column("channel_sessions", sa.Column("evolution_webhook_url", sa.String(1024), nullable=True))
    op.add_column("channel_sessions", sa.Column("connected_at", sa.DateTime(), nullable=True))
    op.add_column("channel_sessions", sa.Column("disconnected_at", sa.DateTime(), nullable=True))

    # ─── فیلدهای proxy_* برای جدول channel_sessions ───
    op.add_column("channel_sessions", sa.Column("proxy_host", sa.String(255), nullable=True))
    op.add_column("channel_sessions", sa.Column("proxy_port", sa.Integer(), nullable=True))
    op.add_column("channel_sessions", sa.Column("proxy_protocol", sa.String(16), nullable=True, server_default="http"))
    op.add_column("channel_sessions", sa.Column("proxy_username", sa.String(255), nullable=True))
    op.add_column("channel_sessions", sa.Column("proxy_password_ciphertext", sa.Text(), nullable=True))
    op.add_column("channel_sessions", sa.Column("proxy_pool_id", sa.String(64), nullable=True))
    op.add_column("channel_sessions", sa.Column("proxy_assigned_at", sa.DateTime(), nullable=True))

    # ─── index برای instance_name ───
    op.create_index("ix_channel_sessions_instance_name", "channel_sessions", ["instance_name"], unique=True)

    # ─── فیلد evolution_metadata برای جدول accounts ───
    op.add_column("accounts", sa.Column("evolution_metadata", JSONB(), nullable=True))

    # ─── ایجاد جدول جدید evolution_webhook_events ───
    op.create_table(
        "evolution_webhook_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("instance_name", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("platform_message_id", sa.String(255), nullable=True),
        sa.Column("remote_jid", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("raw_payload", JSONB(), nullable=True),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("message_attempt_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["message_attempt_id"], ["message_attempts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evolution_webhook_events_timestamp", "evolution_webhook_events", ["created_at"])
    op.create_index("ix_evolution_webhook_events_instance", "evolution_webhook_events", ["instance_name"])
    op.create_index("ix_evolution_webhook_events_message_id", "evolution_webhook_events", ["platform_message_id"])


def downgrade() -> None:
    # ─── حذف جدول جدید ───
    op.drop_index("ix_evolution_webhook_events_message_id", table_name="evolution_webhook_events")
    op.drop_index("ix_evolution_webhook_events_instance", table_name="evolution_webhook_events")
    op.drop_index("ix_evolution_webhook_events_timestamp", table_name="evolution_webhook_events")
    op.drop_table("evolution_webhook_events")

    # ─── حذف فیلد از accounts ───
    op.drop_column("accounts", "evolution_metadata")

    # ─── حذف index ───
    op.drop_index("ix_channel_sessions_instance_name", table_name="channel_sessions")

    # ─── حذف فیلدهای proxy_* از channel_sessions ───
    op.drop_column("channel_sessions", "proxy_assigned_at")
    op.drop_column("channel_sessions", "proxy_pool_id")
    op.drop_column("channel_sessions", "proxy_password_ciphertext")
    op.drop_column("channel_sessions", "proxy_username")
    op.drop_column("channel_sessions", "proxy_protocol")
    op.drop_column("channel_sessions", "proxy_port")
    op.drop_column("channel_sessions", "proxy_host")

    # ─── حذف فیلدهای evolution_* از channel_sessions ───
    op.drop_column("channel_sessions", "disconnected_at")
    op.drop_column("channel_sessions", "connected_at")
    op.drop_column("channel_sessions", "evolution_webhook_url")
    op.drop_column("channel_sessions", "evolution_profile_name")
    op.drop_column("channel_sessions", "evolution_phone")
    op.drop_column("channel_sessions", "evolution_qr_code")
    op.drop_column("channel_sessions", "evolution_status")
    op.drop_column("channel_sessions", "instance_name")
