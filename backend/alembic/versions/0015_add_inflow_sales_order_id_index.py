"""add index on inflow_sales_order_id

Revision ID: 0015_add_inflow_sales_order_id_index
Revises: 0014_add_print_jobs
Create Date: 2026-05-02 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "0015_add_inflow_sales_order_id_index"
down_revision = "0014_add_print_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_orders_inflow_sales_order_id",
        "orders",
        ["inflow_sales_order_id"],
        mysql_length=255,
    )


def downgrade() -> None:
    op.drop_index("ix_orders_inflow_sales_order_id", table_name="orders")
