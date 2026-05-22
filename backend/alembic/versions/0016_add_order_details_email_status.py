"""Add order details email status to orders.

Revision ID: 0016_add_order_details_email_status
Revises: 0015_add_inflow_sales_order_id_index
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_add_order_details_email_status"
down_revision = "0015_add_inflow_sales_order_id_index"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("order_details_email_status", sa.String(length=50), nullable=True))
    op.add_column(
        "orders",
        sa.Column("order_details_email_status_updated_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("orders", "order_details_email_status_updated_at")
    op.drop_column("orders", "order_details_email_status")
