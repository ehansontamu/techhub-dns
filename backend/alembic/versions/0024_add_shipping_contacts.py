"""Add imported FedEx address-book contacts."""
from alembic import op
import sqlalchemy as sa
revision = "0024_shipping_contacts"
down_revision = "0023_add_shipments"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("shipping_contacts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("recipient", sa.JSON(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("imported_at", sa.DateTime(), nullable=False),
        sa.Column("imported_by", sa.String(255)))

def downgrade():
    op.drop_table("shipping_contacts")
