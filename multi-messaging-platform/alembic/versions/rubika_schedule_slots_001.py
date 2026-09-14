"""Allow up to three send-window slots per Rubika phase."""

from alembic import op
import sqlalchemy as sa

revision = "rubika_schedule_slots_001"
down_revision = "contact_soft_delete_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rubika_sender_schedules",
        sa.Column("slot", sa.Integer(), nullable=False, server_default="1"),
    )

    op.drop_constraint(
        "uq_rubika_sender_schedules_phase",
        "rubika_sender_schedules",
        type_="unique",
    )

    op.create_check_constraint(
        "ck_rubika_sender_schedules_slot_1_3",
        "rubika_sender_schedules",
        "slot BETWEEN 1 AND 3",
    )

    op.create_unique_constraint(
        "uq_rubika_sender_schedules_phase_slot",
        "rubika_sender_schedules",
        ["phase", "slot"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_rubika_sender_schedules_phase_slot",
        "rubika_sender_schedules",
        type_="unique",
    )

    op.drop_constraint(
        "ck_rubika_sender_schedules_slot_1_3",
        "rubika_sender_schedules",
        type_="check",
    )

    op.execute("DELETE FROM rubika_sender_schedules WHERE slot <> 1")

    op.drop_column("rubika_sender_schedules", "slot")

    op.create_unique_constraint(
        "uq_rubika_sender_schedules_phase",
        "rubika_sender_schedules",
        ["phase"],
    )
