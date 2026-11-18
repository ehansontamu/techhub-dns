"""Add product weights and FedEx packing/shipment records."""

from alembic import op
import sqlalchemy as sa

revision = "0023_add_shipments"
down_revision = "0022_compat_bundle_ready"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shipping_products",
        sa.Column("product_id", sa.String(255), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("sku", sa.String(255)),
        sa.Column("weight_lb", sa.Numeric(10, 3)),
        sa.Column("updated_by", sa.String(255)),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "shipments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("environment", sa.String(20)),
        sa.Column("draft", sa.JSON(), nullable=False),
        sa.Column("request_payload", sa.JSON()),
        sa.Column("carrier_response", sa.JSON()),
        sa.Column("packages", sa.JSON()),
        sa.Column("master_tracking_number", sa.String(100)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_by", sa.String(255)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("shipments")
    op.drop_table("shipping_products")
