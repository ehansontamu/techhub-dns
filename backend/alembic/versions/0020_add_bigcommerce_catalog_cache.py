"""Add BigCommerce catalog cache tables.

Revision ID: 0020_add_bigcommerce_catalog_cache
Revises: 0019_add_bigcommerce_analytics_cache
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_add_bigcommerce_catalog_cache"
down_revision = "0019_add_bigcommerce_analytics_cache"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bc_brands",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("page_title", sa.String(length=255), nullable=True),
        sa.Column("meta_keywords", sa.Text(), nullable=True),
        sa.Column("meta_description", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("search_keywords", sa.Text(), nullable=True),
        sa.Column("raw_brand", sa.JSON(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bc_brands_name", "bc_brands", ["name"])

    op.create_table(
        "bc_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_visible", sa.Boolean(), nullable=True),
        sa.Column("page_title", sa.String(length=255), nullable=True),
        sa.Column("search_keywords", sa.Text(), nullable=True),
        sa.Column("custom_url", sa.String(length=500), nullable=True),
        sa.Column("raw_category", sa.JSON(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bc_categories_name", "bc_categories", ["name"])
    op.create_index("ix_bc_categories_parent_id", "bc_categories", ["parent_id"])

    op.create_table(
        "bc_products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=True),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("type", sa.String(length=50), nullable=True),
        sa.Column("brand_id", sa.Integer(), nullable=True),
        sa.Column("price", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("cost_price", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("retail_price", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("sale_price", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("calculated_price", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("inventory_level", sa.Integer(), nullable=True),
        sa.Column("inventory_tracking", sa.String(length=50), nullable=True),
        sa.Column("availability", sa.String(length=100), nullable=True),
        sa.Column("condition", sa.String(length=100), nullable=True),
        sa.Column("is_visible", sa.Boolean(), nullable=True),
        sa.Column("custom_url", sa.String(length=500), nullable=True),
        sa.Column("category_ids", sa.JSON(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=True),
        sa.Column("manufacturer", sa.String(length=100), nullable=True),
        sa.Column("cpu_family", sa.String(length=100), nullable=True),
        sa.Column("product_kind", sa.String(length=100), nullable=True),
        sa.Column("raw_product", sa.JSON(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bc_products_brand_id", "bc_products", ["brand_id"])
    op.create_index("ix_bc_products_cpu_family", "bc_products", ["cpu_family"])
    op.create_index("ix_bc_products_is_visible", "bc_products", ["is_visible"])
    op.create_index("ix_bc_products_manufacturer", "bc_products", ["manufacturer"])
    op.create_index("ix_bc_products_name", "bc_products", ["name"])
    op.create_index("ix_bc_products_product_kind", "bc_products", ["product_kind"])
    op.create_index("ix_bc_products_sku", "bc_products", ["sku"])

    op.create_table(
        "bc_product_variants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("price", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("calculated_price", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("cost_price", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column("inventory_level", sa.Integer(), nullable=True),
        sa.Column("purchasing_disabled", sa.Boolean(), nullable=True),
        sa.Column("option_values", sa.JSON(), nullable=True),
        sa.Column("raw_variant", sa.JSON(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["bc_products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bc_product_variants_product_id", "bc_product_variants", ["product_id"])
    op.create_index("ix_bc_product_variants_sku", "bc_product_variants", ["sku"])


def downgrade():
    op.drop_table("bc_product_variants")

    op.drop_table("bc_products")

    op.drop_table("bc_categories")

    op.drop_table("bc_brands")
