import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text

from app.database import Base


class BigCommerceSyncRun(Base):
    __tablename__ = "bc_sync_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    mode = Column(String(50), nullable=False, default="incremental")
    status = Column(String(50), nullable=False, default="running", index=True)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    completed_at = Column(DateTime, nullable=True)
    min_date_modified = Column(DateTime, nullable=True)
    max_date_modified = Column(DateTime, nullable=True)
    orders_scanned = Column(Integer, nullable=False, default=0)
    orders_upserted = Column(Integer, nullable=False, default=0)
    line_items_upserted = Column(Integer, nullable=False, default=0)
    addresses_upserted = Column(Integer, nullable=False, default=0)
    customers_upserted = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    sync_metadata = Column(JSON, nullable=True)


class BigCommerceCustomer(Base):
    __tablename__ = "bc_customers"

    id = Column(Integer, primary_key=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    full_name = Column(String(511), nullable=True, index=True)
    email = Column(String(255), nullable=True, index=True)
    company = Column(String(255), nullable=True, index=True)
    raw_customer = Column(JSON, nullable=True)
    synced_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BigCommerceBrand(Base):
    __tablename__ = "bc_brands"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=True, index=True)
    page_title = Column(String(255), nullable=True)
    meta_keywords = Column(Text, nullable=True)
    meta_description = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)
    search_keywords = Column(Text, nullable=True)
    raw_brand = Column(JSON, nullable=True)
    synced_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BigCommerceCategory(Base):
    __tablename__ = "bc_categories"

    id = Column(Integer, primary_key=True)
    parent_id = Column(Integer, nullable=True, index=True)
    name = Column(String(255), nullable=True, index=True)
    description = Column(Text, nullable=True)
    is_visible = Column(sa.Boolean, nullable=True)
    page_title = Column(String(255), nullable=True)
    search_keywords = Column(Text, nullable=True)
    custom_url = Column(String(500), nullable=True)
    raw_category = Column(JSON, nullable=True)
    synced_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BigCommerceProduct(Base):
    __tablename__ = "bc_products"

    __table_args__ = (
        Index("ix_bc_products_name", "name"),
        Index("ix_bc_products_sku", "sku"),
        Index("ix_bc_products_brand_id", "brand_id"),
        Index("ix_bc_products_is_visible", "is_visible"),
        Index("ix_bc_products_manufacturer", "manufacturer"),
        Index("ix_bc_products_cpu_family", "cpu_family"),
        Index("ix_bc_products_product_kind", "product_kind"),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(500), nullable=True)
    sku = Column(String(255), nullable=True)
    type = Column(String(50), nullable=True)
    brand_id = Column(Integer, nullable=True)
    price = Column(Numeric(18, 4), nullable=False, server_default="0")
    cost_price = Column(Numeric(18, 4), nullable=False, server_default="0")
    retail_price = Column(Numeric(18, 4), nullable=False, server_default="0")
    sale_price = Column(Numeric(18, 4), nullable=False, server_default="0")
    calculated_price = Column(Numeric(18, 4), nullable=False, server_default="0")
    inventory_level = Column(Integer, nullable=True)
    inventory_tracking = Column(String(50), nullable=True)
    availability = Column(String(100), nullable=True)
    condition = Column(String(100), nullable=True)
    is_visible = Column(sa.Boolean, nullable=True)
    custom_url = Column(String(500), nullable=True)
    category_ids = Column(JSON, nullable=True)
    description = Column(Text, nullable=True)
    search_text = Column(Text, nullable=True)
    manufacturer = Column(String(100), nullable=True)
    cpu_family = Column(String(100), nullable=True)
    product_kind = Column(String(100), nullable=True)
    raw_product = Column(JSON, nullable=True)
    synced_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BigCommerceProductVariant(Base):
    __tablename__ = "bc_product_variants"

    __table_args__ = (
        Index("ix_bc_product_variants_product_id", "product_id"),
        Index("ix_bc_product_variants_sku", "sku"),
    )

    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey("bc_products.id", ondelete="CASCADE"), nullable=False)
    sku = Column(String(255), nullable=True)
    price = Column(Numeric(18, 4), nullable=False, server_default="0")
    calculated_price = Column(Numeric(18, 4), nullable=False, server_default="0")
    cost_price = Column(Numeric(18, 4), nullable=False, server_default="0")
    inventory_level = Column(Integer, nullable=True)
    purchasing_disabled = Column(sa.Boolean, nullable=True)
    option_values = Column(JSON, nullable=True)
    raw_variant = Column(JSON, nullable=True)
    synced_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ProductIntelligenceItem(Base):
    __tablename__ = "product_intelligence_items"

    __table_args__ = (
        Index("ix_product_intelligence_items_sku", "sku"),
        Index("ix_product_intelligence_items_name", "name"),
        Index("ix_product_intelligence_items_category", "category"),
        Index("ix_product_intelligence_items_closeout", "closeout"),
        Index("ix_product_intelligence_items_architecture", "architecture"),
        Index("ix_product_intelligence_items_gpu_type", "gpu_type"),
    )

    product_id = Column(String(80), primary_key=True)
    sku = Column(String(255), nullable=True)
    name = Column(String(500), nullable=True)
    category = Column(String(255), nullable=True)
    qty = Column(Integer, nullable=False, server_default="0")
    quantity_on_purchase_order = Column(Integer, nullable=False, server_default="0")
    bc_status9 = Column(Integer, nullable=False, server_default="0")
    bc_status7 = Column(Integer, nullable=False, server_default="0")
    normal_price = Column(Numeric(18, 4), nullable=True)
    ab_price = Column(Numeric(18, 4), nullable=True)
    retail_price = Column(Numeric(18, 4), nullable=True)
    closeout = Column(String(10), nullable=True)
    overall_score = Column(Integer, nullable=True)
    cpu_score = Column(Integer, nullable=True)
    gpu_score = Column(Integer, nullable=True)
    memory_score = Column(Integer, nullable=True)
    storage_score = Column(Integer, nullable=True)
    architecture = Column(String(100), nullable=True)
    product_link = Column(String(1000), nullable=True)
    gpu_type = Column(String(100), nullable=True)
    price_by_scheme_id = Column(JSON, nullable=True)
    raw_item = Column(JSON, nullable=True)
    synced_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ProductIntelligencePriceRow(Base):
    __tablename__ = "product_intelligence_price_rows"

    __table_args__ = (
        Index("ix_product_intelligence_price_rows_product", "product_id"),
        Index("ix_product_intelligence_price_rows_scheme", "scheme_id"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id = Column(String(80), ForeignKey("product_intelligence_items.product_id", ondelete="CASCADE"), nullable=False)
    sku = Column(String(255), nullable=True)
    scheme_id = Column(String(80), nullable=True)
    price_type = Column(String(100), nullable=True)
    unit_price = Column(Numeric(18, 4), nullable=True)
    synced_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BigCommerceOrder(Base):
    __tablename__ = "bc_orders"

    __table_args__ = (
        Index("ix_bc_orders_date_created", "date_created"),
        Index("ix_bc_orders_date_modified", "date_modified"),
        Index("ix_bc_orders_status_date_created", "status", "date_created"),
        Index("ix_bc_orders_customer_date_created", "customer_id", "date_created"),
        Index("ix_bc_orders_college_date_created", "college_unit", "date_created"),
    )

    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("bc_customers.id"), nullable=True, index=True)
    date_created = Column(DateTime, nullable=True)
    date_modified = Column(DateTime, nullable=True)
    date_shipped = Column(DateTime, nullable=True)
    status = Column(String(100), nullable=True, index=True)
    status_id = Column(Integer, nullable=True)
    total_inc_tax = Column(Numeric(18, 4), nullable=False, server_default="0")
    subtotal_inc_tax = Column(Numeric(18, 4), nullable=False, server_default="0")
    shipping_cost_inc_tax = Column(Numeric(18, 4), nullable=False, server_default="0")
    items_total = Column(Integer, nullable=False, server_default="0")
    payment_method = Column(String(255), nullable=True)
    customer_message = Column(Text, nullable=True)
    staff_notes = Column(Text, nullable=True)
    billing_first_name = Column(String(255), nullable=True)
    billing_last_name = Column(String(255), nullable=True)
    billing_email = Column(String(255), nullable=True, index=True)
    billing_company = Column(String(255), nullable=True, index=True)
    placed_by_name = Column(String(511), nullable=True, index=True)
    placed_by_email = Column(String(255), nullable=True, index=True)
    placed_by_company = Column(String(255), nullable=True, index=True)
    college_unit = Column(String(255), nullable=True, index=True)
    department_code = Column(String(255), nullable=True, index=True)
    account_numbers = Column(Text, nullable=True)
    recipients = Column(Text, nullable=True)
    form_fields = Column(JSON, nullable=True)
    raw_order = Column(JSON, nullable=True)
    synced_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BigCommerceOrderItem(Base):
    __tablename__ = "bc_order_items"

    __table_args__ = (
        Index("ix_bc_order_items_order_id", "order_id"),
        Index("ix_bc_order_items_product_id", "product_id"),
        Index("ix_bc_order_items_sku", "sku"),
        Index("ix_bc_order_items_name", "name"),
    )

    id = Column(String(80), primary_key=True)
    bigcommerce_line_item_id = Column(Integer, nullable=True)
    order_id = Column(Integer, ForeignKey("bc_orders.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, nullable=True)
    variant_id = Column(Integer, nullable=True)
    name = Column(String(500), nullable=True)
    sku = Column(String(255), nullable=True)
    quantity = Column(Integer, nullable=False, server_default="0")
    total_inc_tax = Column(Numeric(18, 4), nullable=False, server_default="0")
    base_total = Column(Numeric(18, 4), nullable=False, server_default="0")
    raw_product = Column(JSON, nullable=True)
    synced_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BigCommerceOrderAddress(Base):
    __tablename__ = "bc_order_addresses"

    __table_args__ = (
        Index("ix_bc_order_addresses_order_type", "order_id", "address_type"),
        sa.UniqueConstraint(
            "order_id",
            "address_type",
            "source_index",
            name="uq_bc_order_addresses_order_type_index",
        ),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id = Column(Integer, ForeignKey("bc_orders.id", ondelete="CASCADE"), nullable=False)
    address_type = Column(String(50), nullable=False)
    source_index = Column(Integer, nullable=False, default=0)
    bigcommerce_address_id = Column(Integer, nullable=True)
    first_name = Column(String(255), nullable=True)
    last_name = Column(String(255), nullable=True)
    full_name = Column(String(511), nullable=True, index=True)
    email = Column(String(255), nullable=True, index=True)
    company = Column(String(255), nullable=True, index=True)
    street_1 = Column(String(500), nullable=True)
    street_2 = Column(String(500), nullable=True)
    city = Column(String(255), nullable=True)
    state = Column(String(255), nullable=True)
    zip = Column(String(50), nullable=True)
    country = Column(String(255), nullable=True)
    phone = Column(String(100), nullable=True)
    shipping_method = Column(String(255), nullable=True, index=True)
    form_fields = Column(JSON, nullable=True)
    raw_address = Column(JSON, nullable=True)
    synced_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BigCommerceOrderCustomField(Base):
    __tablename__ = "bc_order_custom_fields"

    __table_args__ = (
        Index("ix_bc_order_custom_fields_order_id", "order_id"),
        Index("ix_bc_order_custom_fields_name", "normalized_name"),
        Index("ix_bc_order_custom_fields_value", "field_value"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id = Column(Integer, ForeignKey("bc_orders.id", ondelete="CASCADE"), nullable=False)
    source = Column(String(100), nullable=False)
    field_name = Column(String(255), nullable=False)
    normalized_name = Column(String(255), nullable=False)
    field_value = Column(Text, nullable=True)
    synced_at = Column(DateTime, nullable=False, default=datetime.utcnow)
