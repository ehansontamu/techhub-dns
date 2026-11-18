"""Merge FedEx shipping and upstream delivery-evidence migration branches.

Retain existing revision IDs so current dev databases and databases that tested
an earlier shipping ZIP can both upgrade normally.
"""

revision = "0026_shipping_delivery_merge"
down_revision = ("0025_shipment_orders", "0023_delivery_evidence")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
