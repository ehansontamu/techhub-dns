"""Add BigCommerce analytics cache tables.

Revision ID: 0019_add_bigcommerce_analytics_cache
Revises: 0018_add_hidden_order_fields
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_add_bigcommerce_analytics_cache"
down_revision = "0018_add_hidden_order_fields"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bc_sync_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("mode", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("min_date_modified", sa.DateTime(), nullable=True),
        sa.Column("max_date_modified", sa.DateTime(), nullable=True),
        sa.Column("orders_scanned", sa.Integer(), nullable=False),
        sa.Column("orders_upserted", sa.Integer(), nullable=False),
        sa.Column("line_items_upserted", sa.Integer(), nullable=False),
        sa.Column("addresses_upserted", sa.Integer(), nullable=False),
        sa.Column("customers_upserted", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sync_metadata", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bc_sync_runs_started_at", "bc_sync_runs", ["started_at"])
    op.create_index("ix_bc_sync_runs_status", "bc_sync_runs", ["status"])

    op.create_table(
        "bc_customers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column("last_name", sa.String(length=255), nullable=True),
        sa.Column("full_name", sa.String(length=511), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("company", sa.String(length=255), nullable=True),
        sa.Column("raw_customer", sa.JSON(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bc_customers_company", "bc_customers", ["company"])
    op.create_index("ix_bc_customers_email", "bc_customers", ["email"])
    op.create_index("ix_bc_customers_full_name", "bc_customers", ["full_name"])

    op.create_table(
        "bc_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("date_created", sa.DateTime(), nullable=True),
        sa.Column("date_modified", sa.DateTime(), nullable=True),
        sa.Column("date_shipped", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=100), nullable=True),
        sa.Column("status_id", sa.Integer(), nullable=True),
        sa.Column("total_inc_tax", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("subtotal_inc_tax", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("shipping_cost_inc_tax", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("items_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("payment_method", sa.String(length=255), nullable=True),
        sa.Column("customer_message", sa.Text(), nullable=True),
        sa.Column("staff_notes", sa.Text(), nullable=True),
        sa.Column("billing_first_name", sa.String(length=255), nullable=True),
        sa.Column("billing_last_name", sa.String(length=255), nullable=True),
        sa.Column("billing_email", sa.String(length=255), nullable=True),
        sa.Column("billing_company", sa.String(length=255), nullable=True),
        sa.Column("placed_by_name", sa.String(length=511), nullable=True),
        sa.Column("placed_by_email", sa.String(length=255), nullable=True),
        sa.Column("placed_by_company", sa.String(length=255), nullable=True),
        sa.Column("college_unit", sa.String(length=255), nullable=True),
        sa.Column("department_code", sa.String(length=255), nullable=True),
        sa.Column("account_numbers", sa.Text(), nullable=True),
        sa.Column("recipients", sa.Text(), nullable=True),
        sa.Column("form_fields", sa.JSON(), nullable=True),
        sa.Column("raw_order", sa.JSON(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["bc_customers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bc_orders_account_numbers", "bc_orders", ["account_numbers"], mysql_length=255)
    op.create_index("ix_bc_orders_billing_company", "bc_orders", ["billing_company"])
    op.create_index("ix_bc_orders_billing_email", "bc_orders", ["billing_email"])
    op.create_index("ix_bc_orders_college_date_created", "bc_orders", ["college_unit", "date_created"])
    op.create_index("ix_bc_orders_college_unit", "bc_orders", ["college_unit"])
    op.create_index("ix_bc_orders_customer_date_created", "bc_orders", ["customer_id", "date_created"])
    op.create_index("ix_bc_orders_customer_id", "bc_orders", ["customer_id"])
    op.create_index("ix_bc_orders_date_created", "bc_orders", ["date_created"])
    op.create_index("ix_bc_orders_date_modified", "bc_orders", ["date_modified"])
    op.create_index("ix_bc_orders_department_code", "bc_orders", ["department_code"])
    op.create_index("ix_bc_orders_placed_by_company", "bc_orders", ["placed_by_company"])
    op.create_index("ix_bc_orders_placed_by_email", "bc_orders", ["placed_by_email"])
    op.create_index("ix_bc_orders_placed_by_name", "bc_orders", ["placed_by_name"])
    op.create_index("ix_bc_orders_status", "bc_orders", ["status"])
    op.create_index("ix_bc_orders_status_date_created", "bc_orders", ["status", "date_created"])

    op.create_table(
        "bc_order_items",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("bigcommerce_line_item_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("variant_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=500), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("quantity", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_inc_tax", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("base_total", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("raw_product", sa.JSON(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["bc_orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bc_order_items_name", "bc_order_items", ["name"])
    op.create_index("ix_bc_order_items_order_id", "bc_order_items", ["order_id"])
    op.create_index("ix_bc_order_items_product_id", "bc_order_items", ["product_id"])
    op.create_index("ix_bc_order_items_sku", "bc_order_items", ["sku"])

    op.create_table(
        "bc_order_addresses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("address_type", sa.String(length=50), nullable=False),
        sa.Column("source_index", sa.Integer(), nullable=False),
        sa.Column("bigcommerce_address_id", sa.Integer(), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column("last_name", sa.String(length=255), nullable=True),
        sa.Column("full_name", sa.String(length=511), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("company", sa.String(length=255), nullable=True),
        sa.Column("street_1", sa.String(length=500), nullable=True),
        sa.Column("street_2", sa.String(length=500), nullable=True),
        sa.Column("city", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=255), nullable=True),
        sa.Column("zip", sa.String(length=50), nullable=True),
        sa.Column("country", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=100), nullable=True),
        sa.Column("shipping_method", sa.String(length=255), nullable=True),
        sa.Column("form_fields", sa.JSON(), nullable=True),
        sa.Column("raw_address", sa.JSON(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["bc_orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "address_type", "source_index", name="uq_bc_order_addresses_order_type_index"),
    )
    op.create_index("ix_bc_order_addresses_company", "bc_order_addresses", ["company"])
    op.create_index("ix_bc_order_addresses_email", "bc_order_addresses", ["email"])
    op.create_index("ix_bc_order_addresses_full_name", "bc_order_addresses", ["full_name"])
    op.create_index("ix_bc_order_addresses_order_type", "bc_order_addresses", ["order_id", "address_type"])
    op.create_index("ix_bc_order_addresses_shipping_method", "bc_order_addresses", ["shipping_method"])

    op.create_table(
        "bc_order_custom_fields",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("field_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("field_value", sa.Text(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["bc_orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bc_order_custom_fields_name", "bc_order_custom_fields", ["normalized_name"])
    op.create_index("ix_bc_order_custom_fields_order_id", "bc_order_custom_fields", ["order_id"])
    op.create_index("ix_bc_order_custom_fields_value", "bc_order_custom_fields", ["field_value"], mysql_length=255)


def downgrade():
    op.drop_table("bc_order_custom_fields")

    op.drop_table("bc_order_addresses")

    op.drop_table("bc_order_items")

    op.drop_table("bc_orders")

    op.drop_table("bc_customers")

    op.drop_table("bc_sync_runs")
