"""Add collaborative compatibility editor tables.

Revision ID: 0020_add_compatibility_editor
Revises: 0019_add_order_list_archive_fields
"""

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "0020_add_compatibility_editor"
down_revision = "0019_add_order_list_archive_fields"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "compatibility_editor_state",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_since", sa.DateTime(), nullable=True),
        sa.Column("last_published_at", sa.DateTime(), nullable=True),
        sa.Column("last_publish_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_publish_error", sa.Text(), nullable=True),
        sa.Column("published_sha256", sa.String(length=64), nullable=True),
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
        sa.Column("document_extra_data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    state_table = sa.table(
        "compatibility_editor_state",
        sa.column("id", sa.String()),
        sa.column("revision", sa.Integer()),
        sa.column("published_revision", sa.Integer()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    now = datetime.utcnow()
    op.bulk_insert(
        state_table,
        [
            {
                "id": "primary",
                "revision": 0,
                "published_revision": 0,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )
    op.create_table(
        "compatibility_computers",
        sa.Column("sku", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("student_edited", sa.Boolean(), nullable=True),
        sa.Column("extra_data", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("sku"),
    )
    op.create_table(
        "compatibility_docks",
        sa.Column("sku", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("student_edited", sa.Boolean(), nullable=True),
        sa.Column("extra_data", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("sku"),
    )
    op.create_table(
        "compatibility_cells",
        sa.Column("computer_sku", sa.String(length=100), nullable=False),
        sa.Column("dock_sku", sa.String(length=100), nullable=False),
        sa.Column("compatibility_status", sa.String(length=50), nullable=True),
        sa.Column("display", sa.String(length=50), nullable=True),
        sa.Column("charging", sa.String(length=50), nullable=True),
        sa.Column("usb_detection", sa.String(length=50), nullable=True),
        sa.Column("ethernet", sa.String(length=50), nullable=True),
        sa.Column("audio", sa.String(length=50), nullable=True),
        sa.Column("sd_card", sa.String(length=50), nullable=True),
        sa.Column("reboot_needed", sa.Boolean(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("student_edited", sa.Boolean(), nullable=True),
        sa.Column("extra_data", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["computer_sku"], ["compatibility_computers.sku"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["dock_sku"], ["compatibility_docks.sku"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("computer_sku", "dock_sku"),
    )
    op.create_index(
        "ix_compatibility_cells_dock_sku",
        "compatibility_cells",
        ["dock_sku"],
    )
    op.create_table(
        "compatibility_editor_operations",
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("mutation_type", sa.String(length=50), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("operation_id"),
    )
    op.create_index(
        "ix_compatibility_editor_operations_created_at",
        "compatibility_editor_operations",
        ["created_at"],
    )


def downgrade():
    op.drop_index(
        "ix_compatibility_editor_operations_created_at",
        table_name="compatibility_editor_operations",
    )
    op.drop_table("compatibility_editor_operations")
    op.drop_index("ix_compatibility_cells_dock_sku", table_name="compatibility_cells")
    op.drop_table("compatibility_cells")
    op.drop_table("compatibility_docks")
    op.drop_table("compatibility_computers")
    op.drop_table("compatibility_editor_state")
