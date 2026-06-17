"""extend import and contact tables

Revision ID: f8a2b1c3d4e5
Revises: 33d0f584c551
Create Date: 2026-06-15 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f8a2b1c3d4e5"
down_revision: Union[str, Sequence[str], None] = "33d0f584c551"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

importstatus_enum = postgresql.ENUM(
    "PENDING",
    "PREVIEWED",
    "VALIDATED",
    "COMMITTED",
    "FAILED",
    name="importstatus",
    create_type=False,
)
importrowstatus_enum = postgresql.ENUM(
    "PENDING",
    "VALID",
    "INVALID",
    "DUPLICATE",
    "SKIPPED",
    name="importrowstatus",
    create_type=False,
)


def upgrade() -> None:
    importstatus_enum.create(op.get_bind(), checkfirst=True)
    importrowstatus_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "import_batches",
        sa.Column("original_file_name", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "import_batches",
        sa.Column("file_path", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "import_batches",
        sa.Column(
            "valid_rows_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "import_batches",
        sa.Column(
            "invalid_rows_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "import_batches",
        sa.Column(
            "duplicate_rows_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "import_batches",
        sa.Column(
            "status",
            importstatus_enum,
            nullable=False,
            server_default="PENDING",
        ),
    )
    op.add_column(
        "import_batches",
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column(
        "import_batches",
        sa.Column("committed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_import_batches_status",
        "import_batches",
        ["status"],
        unique=False,
    )

    op.add_column(
        "contacts",
        sa.Column("source_import_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_contacts_source_import_id",
        "contacts",
        "import_batches",
        ["source_import_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_contacts_source_import_id"),
        "contacts",
        ["source_import_id"],
        unique=False,
    )

    op.create_table(
        "import_rows",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "normalized_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "status",
            importrowstatus_enum,
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("is_valid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duplicate_of_contact_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["import_batches.id"]),
        sa.ForeignKeyConstraint(["duplicate_of_contact_id"], ["contacts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_import_rows_batch_id"),
        "import_rows",
        ["batch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_import_rows_status"),
        "import_rows",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_import_rows_batch_id_row_index",
        "import_rows",
        ["batch_id", "row_index"],
        unique=False,
    )

    op.add_column(
        "contacts",
        sa.Column("source_import_row_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_contacts_source_import_row_id",
        "contacts",
        "import_rows",
        ["source_import_row_id"],
        ["id"],
    )

    op.drop_index(op.f("ix_contacts_phone_e164"), table_name="contacts")
    op.create_index(
        op.f("ix_contacts_phone_e164"),
        "contacts",
        ["phone_e164"],
        unique=True,
    )

    op.alter_column("import_batches", "valid_rows_count", server_default=None)
    op.alter_column("import_batches", "invalid_rows_count", server_default=None)
    op.alter_column("import_batches", "duplicate_rows_count", server_default=None)
    op.alter_column("import_batches", "status", server_default=None)
    op.alter_column("import_batches", "updated_at", server_default=None)
    op.alter_column("import_rows", "status", server_default=None)
    op.alter_column("import_rows", "is_valid", server_default=None)
    op.alter_column("import_rows", "created_at", server_default=None)
    op.alter_column("import_rows", "updated_at", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_contacts_phone_e164"), table_name="contacts")
    op.create_index(
        op.f("ix_contacts_phone_e164"),
        "contacts",
        ["phone_e164"],
        unique=False,
    )

    op.drop_constraint(
        "fk_contacts_source_import_row_id",
        "contacts",
        type_="foreignkey",
    )
    op.drop_column("contacts", "source_import_row_id")

    op.drop_index("ix_import_rows_batch_id_row_index", table_name="import_rows")
    op.drop_index(op.f("ix_import_rows_status"), table_name="import_rows")
    op.drop_index(op.f("ix_import_rows_batch_id"), table_name="import_rows")
    op.drop_table("import_rows")

    op.drop_index(op.f("ix_contacts_source_import_id"), table_name="contacts")
    op.drop_constraint("fk_contacts_source_import_id", "contacts", type_="foreignkey")
    op.drop_column("contacts", "source_import_id")

    op.drop_index("ix_import_batches_status", table_name="import_batches")
    op.drop_column("import_batches", "committed_at")
    op.drop_column("import_batches", "updated_at")
    op.drop_column("import_batches", "status")
    op.drop_column("import_batches", "duplicate_rows_count")
    op.drop_column("import_batches", "invalid_rows_count")
    op.drop_column("import_batches", "valid_rows_count")
    op.drop_column("import_batches", "file_path")
    op.drop_column("import_batches", "original_file_name")

    importrowstatus_enum.drop(op.get_bind(), checkfirst=True)
    importstatus_enum.drop(op.get_bind(), checkfirst=True)
