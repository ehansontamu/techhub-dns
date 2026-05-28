"""Add bundle path to orders.

Revision ID: 0017_add_bundle_path_to_orders
Revises: 0016_add_order_details_email_status
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_add_bundle_path_to_orders"
down_revision = "0016_add_order_details_email_status"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("bundle_path", sa.String(length=500), nullable=True))


def downgrade():
    op.drop_column("orders", "bundle_path")
