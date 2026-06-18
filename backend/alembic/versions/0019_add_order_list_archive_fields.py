"""Add order-list archive fields.

Revision ID: 0019_add_order_list_archive_fields
Revises: 0018_add_hidden_order_fields
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_add_order_list_archive_fields"
down_revision = "0018_add_hidden_order_fields"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "orders",
        sa.Column(
            "archived_from_order_list",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "orders",
        sa.Column("archived_from_order_list_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("archived_from_order_list_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("archived_from_order_list_by", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_orders_archived_from_order_list",
        "orders",
        ["archived_from_order_list"],
    )
    op.alter_column("orders", "archived_from_order_list", server_default=None)


def downgrade():
    op.drop_index("ix_orders_archived_from_order_list", table_name="orders")
    op.drop_column("orders", "archived_from_order_list_by")
    op.drop_column("orders", "archived_from_order_list_at")
    op.drop_column("orders", "archived_from_order_list_reason")
    op.drop_column("orders", "archived_from_order_list")
