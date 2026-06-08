"""Add product intelligence cache tables.

Revision ID: 0021_add_product_intelligence_cache
Revises: 0020_add_bigcommerce_catalog_cache
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_add_product_intelligence_cache"
down_revision = "0020_add_bigcommerce_catalog_cache"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "product_intelligence_items",
        sa.Column("product_id", sa.String(length=80), nullable=False),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=500), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("quantity_on_purchase_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("bc_status9", sa.Integer(), server_default="0", nullable=False),
        sa.Column("bc_status7", sa.Integer(), server_default="0", nullable=False),
        sa.Column("normal_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("ab_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("retail_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("closeout", sa.String(length=10), nullable=True),
        sa.Column("overall_score", sa.Integer(), nullable=True),
        sa.Column("cpu_score", sa.Integer(), nullable=True),
        sa.Column("gpu_score", sa.Integer(), nullable=True),
        sa.Column("memory_score", sa.Integer(), nullable=True),
        sa.Column("storage_score", sa.Integer(), nullable=True),
        sa.Column("architecture", sa.String(length=100), nullable=True),
        sa.Column("product_link", sa.String(length=1000), nullable=True),
        sa.Column("gpu_type", sa.String(length=100), nullable=True),
        sa.Column("price_by_scheme_id", sa.JSON(), nullable=True),
        sa.Column("raw_item", sa.JSON(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("product_id"),
    )
    op.create_index("ix_product_intelligence_items_architecture", "product_intelligence_items", ["architecture"])
    op.create_index("ix_product_intelligence_items_category", "product_intelligence_items", ["category"])
    op.create_index("ix_product_intelligence_items_closeout", "product_intelligence_items", ["closeout"])
    op.create_index("ix_product_intelligence_items_gpu_type", "product_intelligence_items", ["gpu_type"])
    op.create_index("ix_product_intelligence_items_name", "product_intelligence_items", ["name"])
    op.create_index("ix_product_intelligence_items_sku", "product_intelligence_items", ["sku"])

    op.create_table(
        "product_intelligence_price_rows",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=80), nullable=False),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("scheme_id", sa.String(length=80), nullable=True),
        sa.Column("price_type", sa.String(length=100), nullable=True),
        sa.Column("unit_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["product_intelligence_items.product_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_intelligence_price_rows_product", "product_intelligence_price_rows", ["product_id"])
    op.create_index("ix_product_intelligence_price_rows_scheme", "product_intelligence_price_rows", ["scheme_id"])


def downgrade():
    op.drop_index("ix_product_intelligence_price_rows_scheme", table_name="product_intelligence_price_rows")
    op.drop_index("ix_product_intelligence_price_rows_product", table_name="product_intelligence_price_rows")
    op.drop_table("product_intelligence_price_rows")

    op.drop_index("ix_product_intelligence_items_sku", table_name="product_intelligence_items")
    op.drop_index("ix_product_intelligence_items_name", table_name="product_intelligence_items")
    op.drop_index("ix_product_intelligence_items_gpu_type", table_name="product_intelligence_items")
    op.drop_index("ix_product_intelligence_items_closeout", table_name="product_intelligence_items")
    op.drop_index("ix_product_intelligence_items_category", table_name="product_intelligence_items")
    op.drop_index("ix_product_intelligence_items_architecture", table_name="product_intelligence_items")
    op.drop_table("product_intelligence_items")

