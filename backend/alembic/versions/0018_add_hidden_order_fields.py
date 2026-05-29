"""Add hidden order fields.

Revision ID: 0018_add_hidden_order_fields
Revises: 0017_add_bundle_path_to_orders
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_add_hidden_order_fields"
down_revision = "0017_add_bundle_path_to_orders"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "orders",
        sa.Column(
            "hidden_from_ops",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("orders", sa.Column("hidden_reason", sa.Text(), nullable=True))
    op.add_column("orders", sa.Column("hidden_at", sa.DateTime(), nullable=True))
    op.add_column("orders", sa.Column("hidden_by", sa.String(length=255), nullable=True))
    op.create_index("ix_orders_hidden_from_ops", "orders", ["hidden_from_ops"])
    op.alter_column("orders", "hidden_from_ops", server_default=None)


def downgrade():
    op.drop_index("ix_orders_hidden_from_ops", table_name="orders")
    op.drop_column("orders", "hidden_by")
    op.drop_column("orders", "hidden_at")
    op.drop_column("orders", "hidden_reason")
    op.drop_column("orders", "hidden_from_ops")
