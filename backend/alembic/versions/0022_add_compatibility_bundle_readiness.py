"""Add readiness tracking for new compatibility item bundles.

Revision ID: 0022_compat_bundle_ready
Revises: 0021_compat_approval
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_compat_bundle_ready"
down_revision = "0021_compat_approval"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "compatibility_change_requests",
        sa.Column(
            "ready_for_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade():
    op.drop_column("compatibility_change_requests", "ready_for_review")
