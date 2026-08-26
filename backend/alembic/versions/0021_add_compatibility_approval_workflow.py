"""Add compatibility approval workflow and explicit publication snapshots.

Revision ID: 0021_compat_approval
Revises: 0020_add_compatibility_editor
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "0021_compat_approval"
down_revision = "0020_add_compatibility_editor"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "compatibility_editor_state",
        sa.Column(
            "review_revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_table(
        "compatibility_change_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("mutation_type", sa.String(length=50), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "proposal_version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("proposed_data", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("submitted_by", sa.String(length=255), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_compatibility_change_requests_status_target",
        "compatibility_change_requests",
        ["status", "target"],
    )
    op.create_table(
        "compatibility_publication_snapshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "content",
            sa.Text().with_variant(mysql.LONGTEXT(), "mysql"),
            nullable=False,
        ),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("requested_by", sa.String(length=255), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_compatibility_publication_snapshots_revision",
        "compatibility_publication_snapshots",
        ["revision"],
    )
    op.create_index(
        "ix_compatibility_publication_snapshots_status",
        "compatibility_publication_snapshots",
        ["status"],
    )

def downgrade():
    op.drop_index(
        "ix_compatibility_publication_snapshots_status",
        table_name="compatibility_publication_snapshots",
    )
    op.drop_index(
        "ix_compatibility_publication_snapshots_revision",
        table_name="compatibility_publication_snapshots",
    )
    op.drop_table("compatibility_publication_snapshots")
    op.drop_index(
        "ix_compatibility_change_requests_status_target",
        table_name="compatibility_change_requests",
    )
    op.drop_table("compatibility_change_requests")
    op.drop_column("compatibility_editor_state", "review_revision")
