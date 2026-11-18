"""Track all orders in combined shipments and retain existing order reservations."""
from alembic import op
import sqlalchemy as sa
revision = "0025_shipment_orders"
down_revision = "0024_shipping_contacts"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("shipment_orders",
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id"), primary_key=True),
        sa.Column("shipment_id", sa.String(36), sa.ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False))
    op.create_index("ix_shipment_orders_shipment_id", "shipment_orders", ["shipment_id"])
    op.execute("INSERT INTO shipment_orders (order_id, shipment_id) SELECT order_id, id FROM shipments")

def downgrade():
    op.drop_index("ix_shipment_orders_shipment_id", table_name="shipment_orders")
    op.drop_table("shipment_orders")
