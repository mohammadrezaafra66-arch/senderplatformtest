"""add_telegram_mtproto_tables

Revision ID: be16dc0cb029
Revises: 
Create Date: 2026-07-18

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'be16dc0cb029'
down_revision = "add_warming_started_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'telegram_account_pool',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('warm_up_started_at', sa.DateTime(), nullable=True),
        sa.Column('is_warmed_up', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('daily_cap_today', sa.Integer(), nullable=False, server_default='10'),
        sa.Column('sent_today', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_count_reset_date', sa.DateTime(), nullable=False),
        sa.Column('is_healthy', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('last_error_at', sa.DateTime(), nullable=True),
        sa.Column('last_error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_id')
    )

    op.create_table(
        'telegram_sender_schedules',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('start_hour', sa.Integer(), nullable=False, server_default='9'),
        sa.Column('end_hour', sa.Integer(), nullable=False, server_default='21'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_table(
        'telegram_global_sent_registry',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('phone_number', sa.String(20), nullable=False),
        sa.Column('first_sent_at', sa.DateTime(), nullable=False),
        sa.Column('send_count', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('last_sent_campaign_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['last_sent_campaign_id'], ['campaigns.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('phone_number')
    )
    op.create_index('ix_telegram_global_sent_registry_phone_number',
                    'telegram_global_sent_registry', ['phone_number'])

    op.create_table(
        'telegram_mtproto_leads',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('phone_number', sa.String(20), nullable=False),
        sa.Column('telegram_user_id', sa.String(64), nullable=True),
        sa.Column('username', sa.String(255), nullable=True),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False),
        sa.Column('last_activity_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_telegram_mtproto_leads_phone_number',
                    'telegram_mtproto_leads', ['phone_number'])


def downgrade() -> None:
    op.drop_index('ix_telegram_mtproto_leads_phone_number', table_name='telegram_mtproto_leads')
    op.drop_table('telegram_mtproto_leads')
    op.drop_index('ix_telegram_global_sent_registry_phone_number', table_name='telegram_global_sent_registry')
    op.drop_table('telegram_global_sent_registry')
    op.drop_table('telegram_sender_schedules')
    op.drop_table('telegram_account_pool')
