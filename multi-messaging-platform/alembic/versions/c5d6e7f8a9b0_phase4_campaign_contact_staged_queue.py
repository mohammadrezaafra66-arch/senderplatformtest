"""phase 4 step 1: campaign, contact, staged_queue_items

Revision ID: c5d6e7f8a9b0
Revises: b4e5f6a7c8d9
Create Date: 2026-06-15 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, Sequence[str], None] = "b4e5f6a7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Campaign Phase 4 columns ---
    op.add_column("campaigns", sa.Column("name", sa.String(length=512), nullable=True))
    op.add_column("campaigns", sa.Column("channel", sa.String(length=32), nullable=True))
    op.add_column("campaigns", sa.Column("intent", sa.String(length=255), nullable=True))
    op.add_column("campaigns", sa.Column("message_goal", sa.Text(), nullable=True))
    op.add_column("campaigns", sa.Column("max_contacts", sa.Integer(), nullable=True))
    op.add_column("campaigns", sa.Column("daily_limit", sa.Integer(), nullable=True))

    op.execute(
        """
        UPDATE campaigns
        SET name = title
        WHERE name IS NULL
        """
    )
    op.execute(
        """
        UPDATE campaigns
        SET channel = lower(platform::text)
        WHERE channel IS NULL
        """
    )

    op.alter_column("campaigns", "name", nullable=False)
    op.alter_column("campaigns", "channel", nullable=False)

    op.execute(
        """
        ALTER TABLE campaigns
        ALTER COLUMN status TYPE VARCHAR(32)
        USING lower(status::text)
        """
    )
    op.execute(
        """
        UPDATE campaigns
        SET status = 'draft'
        WHERE status IS NULL OR status = ''
        """
    )

    # --- Contact Phase 4 columns ---
    op.add_column("contacts", sa.Column("campaign_id", sa.Integer(), nullable=True))
    op.add_column("contacts", sa.Column("full_name", sa.String(length=512), nullable=True))
    op.add_column("contacts", sa.Column("phone", sa.String(length=32), nullable=True))
    op.add_column("contacts", sa.Column("channel_handle", sa.String(length=255), nullable=True))
    op.add_column("contacts", sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("contacts", sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    op.execute(
        """
        UPDATE contacts
        SET phone = COALESCE(NULLIF(phone_e164, ''), '')
        WHERE phone IS NULL
        """
    )
    op.execute(
        """
        UPDATE contacts
        SET channel_handle = telegram_hint
        WHERE channel_handle IS NULL AND telegram_hint IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE contacts
        SET full_name = trim(
            coalesce(first_name, '') || ' ' || coalesce(last_name, '')
        )
        WHERE full_name IS NULL
          AND (first_name IS NOT NULL OR last_name IS NOT NULL)
        """
    )

    op.alter_column("contacts", "phone", nullable=False)

    # consent_status: migrate enum values then convert to varchar
    op.execute(
        """
        ALTER TABLE contacts
        ALTER COLUMN consent_status TYPE VARCHAR(32)
        USING (
            CASE lower(consent_status::text)
                WHEN 'opted_in' THEN 'allowed'
                WHEN 'opted_out' THEN 'blocked'
                WHEN 'allowed' THEN 'allowed'
                WHEN 'blocked' THEN 'blocked'
                ELSE 'unknown'
            END
        )
        """
    )
    op.execute("DROP TYPE IF EXISTS consentstatus")

    op.create_foreign_key(
        "fk_contacts_campaign_id_campaigns",
        "contacts",
        "campaigns",
        ["campaign_id"],
        ["id"],
    )
    op.create_index("ix_contacts_campaign_id", "contacts", ["campaign_id"], unique=False)
    op.create_index("ix_contacts_phone", "contacts", ["phone"], unique=False)
    op.create_unique_constraint(
        "uq_contact_campaign_phone",
        "contacts",
        ["campaign_id", "phone"],
    )

    # --- staged_queue_items ---
    op.create_table(
        "staged_queue_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("rendered_message_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="staged"),
        sa.Column("final_text", sa.Text(), nullable=False),
        sa.Column("queue_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"]),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"]),
        sa.ForeignKeyConstraint(["rendered_message_id"], ["rendered_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rendered_message_id"),
    )
    op.create_index(
        "ix_staged_queue_items_campaign_id",
        "staged_queue_items",
        ["campaign_id"],
        unique=False,
    )
    op.create_index(
        "ix_staged_queue_items_contact_id",
        "staged_queue_items",
        ["contact_id"],
        unique=False,
    )
    op.create_index(
        "ix_staged_queue_items_status",
        "staged_queue_items",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_staged_queue_items_channel",
        "staged_queue_items",
        ["channel"],
        unique=False,
    )

    op.execute("DROP TYPE IF EXISTS campaignstatus")


def downgrade() -> None:
    op.drop_index("ix_staged_queue_items_channel", table_name="staged_queue_items")
    op.drop_index("ix_staged_queue_items_status", table_name="staged_queue_items")
    op.drop_index("ix_staged_queue_items_contact_id", table_name="staged_queue_items")
    op.drop_index("ix_staged_queue_items_campaign_id", table_name="staged_queue_items")
    op.drop_table("staged_queue_items")

    op.drop_constraint("uq_contact_campaign_phone", "contacts", type_="unique")
    op.drop_index("ix_contacts_phone", table_name="contacts")
    op.drop_index("ix_contacts_campaign_id", table_name="contacts")
    op.drop_constraint("fk_contacts_campaign_id_campaigns", "contacts", type_="foreignkey")

    consentstatus = postgresql.ENUM(
        "UNKNOWN",
        "OPTED_IN",
        "OPTED_OUT",
        name="consentstatus",
        create_type=False,
    )
    consentstatus.create(op.get_bind(), checkfirst=True)
    op.execute(
        """
        ALTER TABLE contacts
        ALTER COLUMN consent_status TYPE consentstatus
        USING (
            CASE lower(consent_status)
                WHEN 'allowed' THEN 'OPTED_IN'::consentstatus
                WHEN 'blocked' THEN 'OPTED_OUT'::consentstatus
                ELSE 'UNKNOWN'::consentstatus
            END
        )
        """
    )

    op.drop_column("contacts", "raw_payload")
    op.drop_column("contacts", "tags")
    op.drop_column("contacts", "channel_handle")
    op.drop_column("contacts", "phone")
    op.drop_column("contacts", "full_name")
    op.drop_column("contacts", "campaign_id")

    campaignstatus = postgresql.ENUM(
        "DRAFT",
        "RUNNING",
        "PAUSED",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        name="campaignstatus",
        create_type=False,
    )
    campaignstatus.create(op.get_bind(), checkfirst=True)
    op.execute(
        """
        ALTER TABLE campaigns
        ALTER COLUMN status TYPE campaignstatus
        USING upper(status)::campaignstatus
        """
    )

    op.drop_column("campaigns", "daily_limit")
    op.drop_column("campaigns", "max_contacts")
    op.drop_column("campaigns", "message_goal")
    op.drop_column("campaigns", "intent")
    op.drop_column("campaigns", "channel")
    op.drop_column("campaigns", "name")
