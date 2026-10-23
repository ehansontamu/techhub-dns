"""Add delivery photo/skip metadata without storing image content.

Revision ID: 0023_delivery_evidence
Revises: 0022_compat_bundle_ready
"""

from alembic import op
import sqlalchemy as sa

revision = "0023_delivery_evidence"
down_revision = "0022_compat_bundle_ready"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("delivery_evidence", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("orders", "delivery_evidence")
