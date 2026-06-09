from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from app.services.bigcommerce_analytics_cache import (
    run_bigcommerce_readonly_query,
)
from app.services.bigcommerce_tool_registry import CHAT_TOOLS, READ_ONLY_TOOLS, call_tool
from app.services.bigcommerce_chat.llm_client import LLM_MAX_ROUNDS, chat_completion
from app.services.bigcommerce_chat.prompts import (
    build_analytics_cache_prompt,
    build_system_prompt,
)

load_dotenv()

ORDER_ADMIN_BASE_URL = os.getenv(
    "BC_ORDER_ADMIN_BASE_URL",
    "https://store-jsj7fos9p1.mybigcommerce.com/manage/orders",
).rstrip("/")

DISPLAY_TIMEZONE_NAME = os.getenv("DISPLAY_TIMEZONE", "America/Chicago")
try:
    DISPLAY_TIMEZONE = ZoneInfo(DISPLAY_TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    DISPLAY_TIMEZONE = None if DISPLAY_TIMEZONE_NAME == "America/Chicago" else timezone.utc


SYSTEM_PROMPT = build_system_prompt()


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "Get a single order status summary by BigCommerce order ID.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "integer"}},
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bigcommerce_analytics_schema",
            "description": "Return the allowed local BigCommerce analytics cache tables, columns, field hints, cache freshness, and SQL rules. Use before writing SQL when you need schema details.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bigcommerce_cache_status",
            "description": "Return local BigCommerce analytics cache freshness, row counts, and the last successful sync.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bigcommerce_readonly_query",
            "description": "Run a bounded read-only SQL SELECT against the local BigCommerce analytics cache. Use this for precise analytics that require grouping, sorting, joining orders to line items, date ranges, or finding first/latest/largest/top records.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A single SELECT query using only allowed local warehouse tables, including bc_orders, bc_order_items, bc_products, product_intelligence_items, product_intelligence_price_rows, and bc_sync_runs.",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 100,
                        "description": "Maximum rows to return. The backend caps this at 200.",
                    },
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_catalog_cache",
            "description": "Search the local BigCommerce catalog cache by text and normalized classifications. Use first for product/site catalog questions before live API catalog calls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search across cached product name, SKU, description, variants, and custom-field text.",
                    },
                    "manufacturer": {
                        "type": "string",
                        "enum": ["Dell", "Apple", "HP", "Lenovo", "Microsoft Surface"],
                    },
                    "cpu_family": {
                        "type": "string",
                        "enum": ["Windows ARM", "Apple silicon", "Intel", "AMD"],
                    },
                    "product_kind": {
                        "type": "string",
                        "enum": ["computer", "display", "printer", "accessory", "unknown"],
                    },
                    "is_visible": {"type": "boolean"},
                    "limit": {"type": "integer", "default": 20},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_catalog_product_profile",
            "description": "Get one cached catalog product by product ID or SKU, including catalog details, variants, categories, normalized classifications, and local sales history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer"},
                    "sku": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_product_intelligence_cache",
            "description": "Search the downloaded Store Intelligence filteredResponse.json product snapshot. Use for current available inventory, quantity on purchase order, closeout flag, BigCommerce status 9 AggieBuy Approval count, status 7 Awaiting Verification count, Normal/AggieBuy/Retail prices, performance scores, architecture, GPU type, and product links.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search across product name, SKU, and category.",
                    },
                    "category": {"type": "string"},
                    "closeout": {
                        "type": "string",
                        "description": "Closeout flag, usually Y or N.",
                    },
                    "architecture": {"type": "string"},
                    "gpu_type": {"type": "string"},
                    "min_qty": {"type": "integer"},
                    "in_stock": {
                        "type": "boolean",
                        "description": "True for qty > 0, false for qty <= 0. Leave unset unless the user asks for in-stock/out-of-stock/available inventory.",
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": [
                            "name",
                            "sku",
                            "category",
                            "qty",
                            "quantity_on_purchase_order",
                            "bc_status9",
                            "bc_status7",
                            "normal_price",
                            "ab_price",
                            "retail_price",
                            "overall_score",
                            "cpu_score",
                            "gpu_score",
                            "memory_score",
                            "storage_score",
                        ],
                        "default": "name",
                    },
                    "sort_direction": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "default": "asc",
                    },
                    "limit": {"type": "integer", "default": 20},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_intelligence_profile",
            "description": "Get one Store Intelligence filteredResponse.json product snapshot by product ID, SKU, or name, including prices, price rows, stock, status counts, performance scores, architecture, GPU type, and raw source item.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string"},
                    "sku": {"type": "string"},
                    "name": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_catalog_classified_product_sales",
            "description": "Rank sold products using cached catalog classifications such as CPU family and laptop/desktop form. Use for questions like most popular Intel laptop sold in 2026 or best-selling AMD desktop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cpu_family": {
                        "type": "string",
                        "enum": ["Windows ARM", "Apple silicon", "Intel", "AMD"],
                    },
                    "machine_form": {
                        "type": "string",
                        "enum": ["computer", "laptop", "desktop"],
                    },
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_catalog_filtered_product_sales",
            "description": "Find live catalog products matching product/spec terms, then aggregate their local sales by product or month. Use for cross-over questions like best-selling AMD CPU computer, month-over-month AMD computer sales, touchscreen laptops sold, visible catalog products with a feature and sales, or product-spec sales leaderboards.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Primary catalog search term, such as AMD, Ryzen, touchscreen, i7, HP, Dell, ProBook.",
                    },
                    "required_terms": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific terms that must appear in the catalog product text/specs, such as ['amd'] or ['ryzen']; avoid generic words like CPU or computer.",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Optional inclusive YYYY-MM-DD order date lower bound.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Optional exclusive YYYY-MM-DD order date upper bound.",
                    },
                    "group_by": {
                        "type": "string",
                        "enum": ["product", "month"],
                        "default": "product",
                        "description": "Use month for month-over-month breakdowns; use product for best-selling product rankings.",
                    },
                    "limit": {"type": "integer", "default": 20},
                    "max_catalog_products": {"type": "integer", "default": 1000},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cpu_family_sales_breakdown",
            "description": "Classify sold machine line items into CPU families, then return quantity percentages and revenue by month or quarter. Use for questions comparing Windows ARM, Apple silicon, Intel, and AMD machine purchases over time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Inclusive YYYY-MM-DD order date lower bound.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Exclusive YYYY-MM-DD order date upper bound.",
                    },
                    "group_by": {
                        "type": "string",
                        "enum": ["month", "quarter"],
                        "default": "month",
                    },
                    "max_catalog_products": {"type": "integer", "default": 1000},
                },
                "required": ["start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_financial_summary",
            "description": "Return order-grain sales and known BigCommerce discount/coupon totals for a date range. Use for sales plus savings/discount questions, because it avoids duplicating order totals across line items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Optional inclusive YYYY-MM-DD order date lower bound.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "Optional exclusive YYYY-MM-DD order date upper bound.",
                    },
                    "include_statuses": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional exact statuses to include, such as ['Completed'].",
                    },
                    "exclude_statuses": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional statuses to exclude. Defaults to Cancelled, Declined, and Refunded.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_identity",
            "description": "Get who placed, received, and shipped on a single order. Use for questions like who placed order 4765.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "integer"}},
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_fulfillment_timing",
            "description": "Return fulfillment timing for one order using creation date, current status, and shipment data when available.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "integer"}},
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_orders_by_customer_name",
            "description": "Find recent orders by customer name, company, email, notes, or message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "days": {"type": "integer", "default": 90},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_recent_orders",
            "description": "List recent orders for broad status, volume, or trend questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "default": 30},
                    "limit": {"type": "integer", "default": 50},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_products",
            "description": "Get read-only product line items for a specific order.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "integer"}},
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_revenue_summary",
            "description": "Return store-wide order revenue totals for a date range. Use for total revenue questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "YYYY-MM-DD inclusive start date.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "YYYY-MM-DD exclusive end date. Omit for through now.",
                    },
                    "max_orders": {"type": "integer", "default": 50000},
                    "exclude_statuses": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": ["Cancelled", "Declined", "Refunded"],
                    },
                },
                "required": ["start_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order_summary",
            "description": "Flexible filtered order summary: revenue, order count, item count, average order value, statuses, and optional matching product metrics.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "days": {"type": "integer", "default": 90},
                    "dimension": {
                        "type": "string",
                        "enum": ["college_unit", "department_code", "account_number", "recipient"],
                    },
                    "value": {"type": "string"},
                    "product_keyword": {"type": "string"},
                    "product_group": {"type": "string", "default": "computers"},
                    "brand": {"type": "string"},
                    "placed_by": {"type": "string"},
                    "billing_contact": {"type": "string"},
                    "include_statuses": {"type": "array", "items": {"type": "string"}},
                    "exclude_statuses": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": ["Cancelled", "Declined", "Refunded"],
                    },
                    "max_orders": {"type": "integer", "default": 50000},
                    "max_line_item_orders": {"type": "integer", "default": 5000},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_shipping_spend_by_method",
            "description": "Summarize customer-facing shipping charges by shipping method/carrier such as FedEx, UPS, or Free On-Campus Delivery. This is not actual carrier invoice spend.",
            "parameters": {
                "type": "object",
                "properties": {
                    "method_keyword": {"type": ["string", "null"]},
                    "start_date": {"type": ["string", "null"], "default": "2000-01-01"},
                    "end_date": {"type": ["string", "null"]},
                    "days": {"type": "integer", "default": 90},
                    "max_orders": {"type": "integer", "default": 50000},
                    "max_shipping_address_orders": {"type": "integer", "default": 5000},
                    "include_statuses": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                    },
                    "exclude_statuses": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                    },
                    "exclude_order_ids": {
                        "type": ["array", "null"],
                        "items": {"type": "integer"},
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_grouped_order_summary",
            "description": "Flexible grouped analytics for filtered orders. Use for breakdowns by month, quarter, status, college/unit, customer, billing contact, product name, or product brand.",
            "parameters": {
                "type": "object",
                "properties": {
                    "group_by": {
                        "type": "string",
                        "enum": [
                            "day",
                            "week",
                            "month",
                            "quarter",
                            "status",
                            "college_unit",
                            "department_code",
                            "account_number",
                            "recipient",
                            "placed_by",
                            "billing_contact",
                            "product_name",
                            "product_brand",
                        ],
                    },
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "days": {"type": "integer", "default": 90},
                    "dimension": {
                        "type": "string",
                        "enum": ["college_unit", "department_code", "account_number", "recipient"],
                    },
                    "value": {"type": "string"},
                    "product_keyword": {"type": "string"},
                    "product_group": {"type": "string", "default": "computers"},
                    "brand": {"type": "string"},
                    "placed_by": {"type": "string"},
                    "billing_contact": {"type": "string"},
                    "include_statuses": {"type": "array", "items": {"type": "string"}},
                    "exclude_statuses": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": ["Cancelled", "Declined", "Refunded"],
                    },
                    "limit": {"type": "integer", "default": 25},
                    "sort_by": {
                        "type": "string",
                        "enum": ["revenue", "orders", "items"],
                        "default": "revenue",
                    },
                    "max_orders": {"type": "integer", "default": 50000},
                    "max_line_item_orders": {"type": "integer", "default": 5000},
                },
                "required": ["group_by"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_source_orders_for_summary",
            "description": "Return source orders and optional matching line items behind a filtered summary for auditability.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                    "days": {"type": "integer", "default": 90},
                    "dimension": {
                        "type": "string",
                        "enum": ["college_unit", "department_code", "account_number", "recipient"],
                    },
                    "value": {"type": "string"},
                    "product_keyword": {"type": "string"},
                    "product_group": {"type": "string", "default": "computers"},
                    "brand": {"type": "string"},
                    "placed_by": {"type": "string"},
                    "billing_contact": {"type": "string"},
                    "include_statuses": {"type": "array", "items": {"type": "string"}},
                    "exclude_statuses": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": ["Cancelled", "Declined", "Refunded"],
                    },
                    "limit": {"type": "integer", "default": 50},
                    "max_orders": {"type": "integer", "default": 50000},
                    "max_line_item_orders": {"type": "integer", "default": 5000},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ranked_orders",
            "description": "Rank orders by a chosen order-level metric for a date range or filter. Use for biggest/largest/highest-value orders, smallest/lowest-value orders, earliest/first submitted order, latest/newest order, first order number, and most/fewest item orders. If no start_date and no days are supplied, this tool searches all time from 2000-01-01.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "YYYY-MM-DD inclusive start date. For all-time questions, omit this or use 2000-01-01.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "YYYY-MM-DD exclusive end date.",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Use only for relative ranges such as last 7 days or last month. Do not pass this for all-time/ever questions.",
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": ["total_inc_tax", "date_created", "order_id", "items_total"],
                        "default": "total_inc_tax",
                        "description": "total_inc_tax for dollar value; date_created for first/earliest/latest submitted order; order_id for lowest/highest order number; items_total for most/fewest items.",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "default": "desc",
                        "description": "desc for biggest/highest/latest/newest/most; asc for smallest/lowest/first/earliest/fewest.",
                    },
                    "limit": {"type": "integer", "default": 10},
                    "dimension": {
                        "type": "string",
                        "enum": ["college_unit", "department_code", "account_number", "recipient"],
                    },
                    "value": {"type": "string"},
                    "placed_by": {"type": "string"},
                    "billing_contact": {"type": "string"},
                    "include_statuses": {"type": "array", "items": {"type": "string"}},
                    "exclude_statuses": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": ["Cancelled", "Declined", "Refunded"],
                    },
                    "max_orders": {"type": "integer", "default": 5000},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_sales_leaderboard",
            "description": "Rank matching products by quantity sold and include the order with the most units for each product. Use for product popularity, top computer/product questions, and 'which order had the most of that product'. For all-time/ever product questions, pass start_date='2000-01-01'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "YYYY-MM-DD inclusive start date. Use 2000-01-01 for all-time questions.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "YYYY-MM-DD exclusive end date.",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Use only for relative ranges such as last 7 days or last month. Do not pass this for all-time/ever questions.",
                    },
                    "product_keyword": {"type": "string"},
                    "product_group": {
                        "type": "string",
                        "description": "Use computers for broad computer/laptop/desktop/workstation questions.",
                    },
                    "brand": {"type": "string"},
                    "dimension": {
                        "type": "string",
                        "enum": ["college_unit", "department_code", "account_number", "recipient"],
                    },
                    "value": {"type": "string"},
                    "placed_by": {"type": "string"},
                    "billing_contact": {"type": "string"},
                    "include_statuses": {"type": "array", "items": {"type": "string"}},
                    "exclude_statuses": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": ["Cancelled", "Declined", "Refunded"],
                    },
                    "limit": {"type": "integer", "default": 10},
                    "max_orders": {"type": "integer", "default": 5000},
                    "max_line_item_orders": {"type": "integer", "default": 500},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_oldest_unfulfilled_orders",
            "description": "Return oldest currently unfulfilled orders, sorted by age since order creation, plus common items on those orders.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "default": 180},
                    "limit": {"type": "integer", "default": 10},
                    "max_orders": {"type": "integer", "default": 50000},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fulfillment_aging_report",
            "description": "Return both longest historical fulfillment durations for fulfilled orders and oldest currently unfulfilled orders.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "default": 180},
                    "limit": {"type": "integer", "default": 10},
                    "max_orders": {"type": "integer", "default": 50000},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_products_sold",
            "description": "Find the most popular specific products sold by quantity over a recent period.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "default": 90},
                    "limit": {"type": "integer", "default": 10},
                    "max_orders": {"type": "integer", "default": 250},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_products_sold_to",
            "description": "Legacy fuzzy text search for popular products sold to a customer/company term. Prefer get_top_products_for_dimension_value for college/unit, department code, account number, or recipient questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_or_department": {"type": "string"},
                    "days": {"type": "integer", "default": 90},
                    "limit": {"type": "integer", "default": 10},
                    "max_orders": {"type": "integer", "default": 250},
                },
                "required": ["customer_or_department"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_customers_for_product_keyword",
            "description": "Find which customers, schools, departments, or groups bought the most products matching a product keyword, brand, or broad product phrase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "days": {"type": "integer", "default": 365},
                    "limit": {"type": "integer", "default": 10},
                    "max_orders": {"type": "integer", "default": 1000},
                    "group_by": {
                        "type": "string",
                        "enum": ["person", "college_unit"],
                        "default": "person",
                    },
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_customers_for_product_keyword_in_dimension",
            "description": "Find which people/customers bought the most products matching a keyword within a checkout dimension value such as a college/unit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "dimension": {
                        "type": "string",
                        "enum": ["college_unit", "department_code", "account_number", "recipient"],
                    },
                    "value": {"type": "string"},
                    "days": {"type": "integer", "default": 365},
                    "limit": {"type": "integer", "default": 10},
                    "max_orders": {"type": "integer", "default": 1000},
                },
                "required": ["keyword", "dimension", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_keyword_order_lines_for_customer",
            "description": "Return exact source order lines for a customer/person and product keyword. Use for follow-ups like what did they order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer": {"type": "string"},
                    "keyword": {"type": "string"},
                    "days": {"type": "integer", "default": 365},
                    "max_orders": {"type": "integer", "default": 1000},
                },
                "required": ["customer", "keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_full_order_contents",
            "description": "Return every product line on explicit order IDs. Use when the user asks for full order contents and provides order IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_ids": {"type": "array", "items": {"type": "integer"}},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": ["order_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_full_order_contents_for_customer_product_in_dimension",
            "description": "Return every product line on orders matching a customer/person, product keyword, and checkout dimension. Use for follow-ups like full content of those orders.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer": {"type": "string"},
                    "keyword": {"type": "string"},
                    "dimension": {
                        "type": "string",
                        "enum": ["college_unit", "department_code", "account_number", "recipient"],
                    },
                    "value": {"type": "string"},
                    "days": {"type": "integer", "default": 365},
                    "limit": {"type": "integer", "default": 50},
                    "max_orders": {"type": "integer", "default": 1000},
                },
                "required": ["customer", "keyword", "dimension", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_full_order_contents_for_placed_by_customer",
            "description": "Return every product line on orders placed by a BigCommerce customer account. Billing/shipping contact matches are reported separately and are not treated as placed-by orders.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer": {"type": "string"},
                    "days": {"type": "integer", "default": 365},
                    "limit": {"type": "integer", "default": 50},
                    "max_orders": {"type": "integer", "default": 1000},
                },
                "required": ["customer"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_computer_brand_sales_since",
            "description": "Compare quantities and percentages of computer sales across brands such as Dell and HP since a start date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "brands": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": ["Dell", "HP"],
                    },
                    "start_date": {
                        "type": "string",
                        "description": "YYYY-MM-DD start date, for example 2026-01-01.",
                        "default": "2026-01-01",
                    },
                    "max_orders": {"type": "integer", "default": 1000},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sales_by_dimension",
            "description": "Break recent sales down by a checkout dimension: college_unit, department_code, account_number, or recipient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {
                        "type": "string",
                        "enum": ["college_unit", "department_code", "account_number", "recipient"],
                        "default": "college_unit",
                    },
                    "days": {"type": "integer", "default": 90},
                    "limit": {"type": "integer", "default": 25},
                    "max_orders": {"type": "integer", "default": 1000},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_orders_for_dimension_value",
            "description": "Return orders matching a checkout dimension value, such as orders for the Bush School or a department code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {
                        "type": "string",
                        "enum": ["college_unit", "department_code", "account_number", "recipient"],
                    },
                    "value": {"type": "string"},
                    "days": {"type": "integer", "default": 90},
                    "limit": {"type": "integer", "default": 50},
                    "max_orders": {"type": "integer", "default": 1000},
                },
                "required": ["dimension", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_products_for_dimension_value",
            "description": "Return top products sold for a checkout dimension value, such as a college/unit, department code, account number, or recipient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {
                        "type": "string",
                        "enum": ["college_unit", "department_code", "account_number", "recipient"],
                    },
                    "value": {"type": "string"},
                    "days": {"type": "integer", "default": 90},
                    "limit": {"type": "integer", "default": 10},
                    "max_orders": {"type": "integer", "default": 1000},
                },
                "required": ["dimension", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_dimension_values",
            "description": "Compare revenue, order count, and item quantity for multiple values of a checkout dimension.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {
                        "type": "string",
                        "enum": ["college_unit", "department_code", "account_number", "recipient"],
                    },
                    "values": {"type": "array", "items": {"type": "string"}},
                    "days": {"type": "integer", "default": 90},
                    "max_orders": {"type": "integer", "default": 1000},
                },
                "required": ["dimension", "values"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "count_orders_for_company",
            "description": "Count recent orders matching a company, department, customer group, or text term.",
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "days": {"type": "integer", "default": 30},
                    "limit": {"type": "integer", "default": 250},
                },
                "required": ["company"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_catalog_products",
            "description": "List or filter live BigCommerce catalog products from the site catalog. Use for questions about products currently on the site, visibility, availability, inventory tracking, category membership, and catalog browsing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "sku": {"type": "string"},
                    "name": {"type": "string"},
                    "category_id": {"type": "integer"},
                    "is_visible": {"type": "boolean"},
                    "availability": {
                        "type": "string",
                        "description": "BigCommerce availability filter such as available, disabled, preorder.",
                    },
                    "inventory_tracking": {
                        "type": "string",
                        "description": "BigCommerce inventory tracking filter such as none, product, variant.",
                    },
                    "page": {"type": "integer", "default": 1},
                    "limit": {"type": "integer", "default": 20},
                    "sort": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_catalog_product",
            "description": "Get live BigCommerce catalog product details by product ID, including variants, images, custom fields, prices, inventory, visibility, and availability.",
            "parameters": {
                "type": "object",
                "properties": {"product_id": {"type": "integer"}},
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Search live BigCommerce catalog products by keyword. Use for product/site catalog lookup, not sales history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_by_sku",
            "description": "Get live BigCommerce catalog product details by SKU.",
            "parameters": {
                "type": "object",
                "properties": {"sku": {"type": "string"}},
                "required": ["sku"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_low_stock_products",
            "description": "Find live BigCommerce catalog products with tracked inventory at or below a threshold.",
            "parameters": {
                "type": "object",
                "properties": {
                    "threshold": {"type": "integer", "default": 5},
                    "limit": {"type": "integer", "default": 250},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_products_missing_images",
            "description": "Find visible live BigCommerce catalog products that have no images.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 250}},
            },
        },
    },
]

CACHE_TOOL_NAMES = {
    "get_bigcommerce_analytics_schema",
    "get_bigcommerce_cache_status",
    "get_catalog_classified_product_sales",
    "get_catalog_filtered_product_sales",
    "get_catalog_product_profile",
    "get_cpu_family_sales_breakdown",
    "get_order_financial_summary",
    "get_product_intelligence_profile",
    "run_bigcommerce_readonly_query",
    "search_catalog_cache",
    "search_product_intelligence_cache",
}
CATALOG_TOOL_NAMES = {
    "list_catalog_products",
    "get_catalog_product",
    "search_products",
    "get_product_by_sku",
    "get_low_stock_products",
    "find_products_missing_images",
}
PRIMARY_TOOL_NAMES = set(CACHE_TOOL_NAMES) | CATALOG_TOOL_NAMES
PRIMARY_TOOL_SCHEMAS = [
    schema
    for schema in TOOL_SCHEMAS
    if schema.get("function", {}).get("name") in PRIMARY_TOOL_NAMES
]

ANALYTICS_CACHE_PROMPT = build_analytics_cache_prompt()

LIVE_TOOL_SCHEMAS = [
    schema
    for schema in TOOL_SCHEMAS
    if schema.get("function", {}).get("name") not in CACHE_TOOL_NAMES
]
LIVE_TOOL_NAMES = {
    schema.get("function", {}).get("name")
    for schema in LIVE_TOOL_SCHEMAS
    if schema.get("function", {}).get("name")
}


WEB_MAX_ORDER_SCAN = 5000
WEB_MAX_LINE_ITEM_ORDER_SCAN = 500


def _clamp_tool_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    clamped = dict(arguments)
    if "max_orders" in clamped:
        try:
            clamped["max_orders"] = max(1, min(int(clamped["max_orders"]), WEB_MAX_ORDER_SCAN))
        except (TypeError, ValueError):
            clamped["max_orders"] = WEB_MAX_ORDER_SCAN

    line_item_keys = [
        "max_line_item_orders",
        "max_shipping_address_orders",
    ]
    for key in line_item_keys:
        if key not in clamped:
            continue
        try:
            clamped[key] = max(1, min(int(clamped[key]), WEB_MAX_LINE_ITEM_ORDER_SCAN))
        except (TypeError, ValueError):
            clamped[key] = WEB_MAX_LINE_ITEM_ORDER_SCAN

    return clamped


def _call_tool(name: str, arguments_json: str) -> str:
    if name not in CHAT_TOOLS:
        return json.dumps({"error": f"Tool is not allowed: {name}"})

    try:
        arguments = _clamp_tool_arguments(json.loads(arguments_json or "{}"))
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"Invalid tool arguments JSON: {exc}"})

    result = call_tool(name, arguments)
    return json.dumps(result, default=str)


def _is_money_column(column: str) -> bool:
    lowered = column.lower()
    return any(
        token in lowered
        for token in (
            "total",
            "revenue",
            "sales",
            "shipping",
            "amount",
            "price",
            "cost",
            "subtotal",
            "discount",
        )
    )


def _is_count_column(column: str) -> bool:
    lowered = column.lower()
    return "count" in lowered or lowered in {"quantity", "qty", "items_total"}


def _format_sql_cell(column: str, value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        if _is_money_column(column):
            return _format_money(float(value))
        return _format_number(value)
    return str(value)


def _format_single_row_sql_aggregate(
    row: dict[str, Any],
    columns: list[str],
) -> str | None:
    if not row or not columns:
        return None

    parts: list[str] = []
    for column in columns:
        value = row.get(column)
        if value is None:
            continue
        label = column.replace("_", " ")
        if _is_money_column(column) or _is_count_column(column) or isinstance(value, (int, float)):
            parts.append(f"{label}: {_format_sql_cell(column, value)}")
        else:
            parts.append(f"{label}: {value}")

    if not parts:
        return None
    return ". ".join(parts) + "."


def _looks_like_year_period_rows(rows: list[dict[str, Any]], columns: list[str]) -> bool:
    if not rows or not columns:
        return False
    label_column = columns[0]
    labels = [str(row.get(label_column) or "").strip() for row in rows]
    return len(labels) >= 2 and all(re.fullmatch(r"20\d{2}", label) for label in labels)


def _format_year_period_sql_rows(
    rows: list[dict[str, Any]],
    columns: list[str],
) -> str | None:
    if not _looks_like_year_period_rows(rows, columns):
        return None

    label_column = columns[0]
    value_columns = [column for column in columns[1:] if column]
    if not value_columns:
        return None

    lines = [
        "Period comparison:",
        "",
        "| " + " | ".join([label_column.replace("_", " ").title(), *value_columns]) + " |",
        "| " + " | ".join(["---", *["---:" for _ in value_columns]]) + " |",
    ]
    for row in rows:
        values = [_format_sql_cell(column, row.get(column)) for column in value_columns]
        lines.append(f"| {row.get(label_column)} | " + " | ".join(values) + " |")
    lines.append("")
    lines.append(
        "Note: this table came directly from SQL. If the periods are not year-to-date through today, ask again with the exact date range you want."
    )
    return "\n".join(lines)


def _format_ranking_sql_rows(
    rows: list[dict[str, Any]],
    columns: list[str],
) -> str | None:
    if _looks_like_year_period_rows(rows, columns):
        return None

    if len(columns) < 2 or not rows:
        return None

    label_column = columns[0]
    value_columns = columns[1:]
    if not any(
        isinstance(row.get(column), (int, float))
        for row in rows
        for column in value_columns
    ):
        return None

    lines: list[str] = []
    for index, row in enumerate(rows, start=1):
        label = row.get(label_column)
        order_id = row.get("order_id") or row.get("id")
        prefix = f"Order {order_id} | " if order_id is not None else ""
        value_parts = [
            f"{column.replace('_', ' ')}: {_format_sql_cell(column, row.get(column))}"
            for column in value_columns
            if row.get(column) is not None
        ]
        lines.append(f"{index}. {prefix}{label} | " + " | ".join(value_parts))

    return "\n".join(lines)


def _format_sql_query_result(result: dict[str, Any], _question: str) -> str | None:
    if result.get("error"):
        return None

    rows = result.get("rows") or []
    columns = result.get("columns") or []
    if not rows:
        return "No matching rows in the local analytics cache."

    if len(rows) == 1:
        aggregate_answer = _format_single_row_sql_aggregate(rows[0], columns)
        if aggregate_answer:
            return aggregate_answer

    if 1 <= len(rows) <= 50 and len(columns) <= 8:
        year_period_answer = _format_year_period_sql_rows(rows, columns)
        if year_period_answer:
            return year_period_answer

        ranking_answer = _format_ranking_sql_rows(rows, columns)
        if ranking_answer:
            if result.get("truncated"):
                ranking_answer += (
                    f"\nNote: results were truncated at {result.get('limit')} rows."
                )
            return ranking_answer

    return None


def _is_sales_history_question(question: str) -> bool:
    normalized = question.lower()
    return any(
        term in normalized
        for term in [
            "sold",
            "sales",
            "revenue",
            "quantity",
            "qty",
            "purchased",
            "bought",
            "ordered",
            "month over month",
            "per month",
        ]
    )


def _format_direct_tool_answer(name: str, result: dict[str, Any], question: str = "") -> str | None:
    if result.get("error"):
        return None
    if name == "run_bigcommerce_readonly_query":
        return _format_sql_query_result(result, "")
    if name in {"get_purchase_breakdown_by_college_unit", "get_sales_by_dimension"}:
        return _format_purchase_breakdown(result)
    if name == "get_revenue_summary":
        return _format_revenue_summary(result)
    if name == "get_order_summary":
        return _format_order_summary(result)
    if name == "get_grouped_order_summary":
        return _format_grouped_order_summary(result)
    if name == "get_ranked_orders":
        return _format_ranked_orders(result)
    if name == "get_product_sales_leaderboard":
        return _format_product_sales_leaderboard(result)
    if name == "get_source_orders_for_summary":
        return _format_source_orders(result)
    if name == "get_shipping_spend_by_method":
        return _format_shipping_spend(result)
    if name == "get_oldest_unfulfilled_orders":
        return _format_oldest_unfulfilled(result)
    if name == "get_fulfillment_aging_report":
        return _format_fulfillment_aging_report(result)
    if name == "get_order_fulfillment_timing":
        return _format_fulfillment_timing(result)
    if name == "get_order_identity":
        return _format_order_identity(result)
    if name in {
        "get_full_order_contents",
        "get_full_order_contents_for_customer_product_in_dimension",
        "get_full_order_contents_for_placed_by_customer",
    }:
        return _format_order_contents(result)
    if name in {
        "list_catalog_products",
        "search_products",
        "search_catalog_cache",
        "get_product_by_sku",
        "get_low_stock_products",
        "find_products_missing_images",
    }:
        if _is_sales_history_question(question):
            return None
        return _format_catalog_products(result)
    if name == "get_catalog_product_profile":
        if _is_sales_history_question(question):
            return None
        return _format_catalog_product_profile(result)
    if name == "get_catalog_filtered_product_sales":
        return _format_catalog_filtered_product_sales(result)
    if name == "get_cpu_family_sales_breakdown":
        return _format_cpu_family_sales_breakdown(result)
    if name == "get_order_financial_summary":
        return _format_order_financial_summary(result)
    if name == "get_catalog_product":
        product = result.get("product") or {}
        return _format_catalog_products({"count": 1 if product else 0, "products": [product] if product else []})
    return None


def _cache_tool_result_failed(name: str, result: dict[str, Any]) -> bool:
    if name in CACHE_TOOL_NAMES and result.get("error"):
        return True
    if name != "run_bigcommerce_readonly_query":
        return False
    cache_status = result.get("cache_status") or {}
    return int(result.get("row_count") or 0) == 0 and int(cache_status.get("order_count") or 0) == 0


def _run_tool_calls(
    tool_calls: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> tuple[str | None, bool]:
    formatted_direct_answer: str | None = None
    cache_failed = False

    for tool_call in tool_calls:
        function = tool_call.get("function") or {}
        name = function.get("name", "")
        arguments = function.get("arguments", "{}")
        if os.getenv("DEBUG_TOOLS") == "1":
            print(f"[tool] {name}({arguments})")
        result = _call_tool(name, arguments)
        parsed = json.loads(result)
        cache_failed = cache_failed or _cache_tool_result_failed(name, parsed)
        user_question = next(
            (
                str(message.get("content") or "")
                for message in reversed(history)
                if message.get("role") == "user"
            ),
            "",
        )
        formatted_direct_answer = _format_direct_tool_answer(name, parsed, user_question) or formatted_direct_answer
        history.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.get("id", name),
                "name": name,
                "content": result,
            }
        )

    return formatted_direct_answer, cache_failed


def _compact_chat_history_for_cache(
    history: list[dict[str, Any]],
    question: str,
) -> list[dict[str, Any]]:
    cache_history: list[dict[str, Any]] = [
        {"role": "system", "content": ANALYTICS_CACHE_PROMPT}
    ]

    recent_messages: list[dict[str, str]] = []
    for message in history:
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
            continue
        recent_messages.append({"role": role, "content": content})

    cache_history.extend(recent_messages[-10:])
    if not cache_history or cache_history[-1].get("content") != question:
        cache_history.append({"role": "user", "content": question})
    return cache_history


def _is_tool_plan_without_answer(answer: str) -> bool:
    lower = answer.lower().strip()
    if not lower:
        return True
    plan_markers = [
        "i'll use",
        "i will use",
        "i'll query",
        "i will query",
        "need use",
        "checking ",
        "run_bigcommerce_readonly_query",
        "get_bigcommerce_analytics_schema",
        "get_catalog_classified_product_sales",
        "get_catalog_filtered_product_sales",
        "get_catalog_product_profile",
        "list_catalog_products",
        "search_products",
        "search_catalog_cache",
        "get_product_by_sku",
        "get_catalog_product",
        "local analytics cache to",
        "catalog products",
        "catalog tool",
        "select ",
        "from bc_",
    ]
    return any(marker in lower for marker in plan_markers)


def _run_primary_cache_chat(
    question: str,
    history: list[dict[str, Any]],
    max_rounds: int | None = None,
) -> tuple[str | None, bool]:
    cache_history = _compact_chat_history_for_cache(history, question)
    saw_cache_failure = False
    round_limit = max_rounds or LLM_MAX_ROUNDS

    for round_index in range(round_limit):
        message = _coerce_text_tool_call(
            chat_completion(
                messages=cache_history,
                tools=PRIMARY_TOOL_SCHEMAS,
                tool_choice="auto",
            ),
            allowed_tool_names=PRIMARY_TOOL_NAMES,
        )

        tool_calls = [
            tool_call
            for tool_call in message.get("tool_calls") or []
            if (tool_call.get("function") or {}).get("name") in PRIMARY_TOOL_NAMES
        ]
        if tool_calls:
            message = dict(message)
            message["tool_calls"] = tool_calls
            cache_history.append(_assistant_history_message(message))
            formatted_direct_answer, cache_failed = _run_tool_calls(tool_calls, cache_history)
            saw_cache_failure = saw_cache_failure or cache_failed
            if formatted_direct_answer:
                return (
                    _prepare_final_answer(formatted_direct_answer, question),
                    saw_cache_failure,
                )
            continue

        answer = _sanitize_assistant_answer(message.get("content") or "")
        if _is_tool_plan_without_answer(answer) and round_index < round_limit - 1:
            cache_history.append({"role": "assistant", "content": answer})
            cache_history.append(
                {
                    "role": "user",
                    "content": (
                        "Use the available read-only SQL tool now if data is needed. "
                        "If tool results are already present, answer from those results. "
                        "Do not describe the plan."
                    ),
                }
            )
            continue

        if answer:
            return _prepare_final_answer(answer, question), saw_cache_failure

    return None, saw_cache_failure


def _format_money(value: float | int) -> str:
    return f"${value:,.0f}"


def _format_number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}"
    if isinstance(value, (int, float)):
        return f"{value:,}"
    return str(value)


def _format_catalog_price(value: Any) -> str:
    if value in (None, ""):
        return "unknown price"
    try:
        return _format_money(float(value))
    except (TypeError, ValueError):
        return str(value)


def _format_catalog_product_line(product: dict[str, Any], index: int) -> str:
    visible = "visible" if product.get("is_visible") else "hidden"
    inventory = product.get("inventory_level")
    inventory_label = (
        f"{_format_number(inventory)} in stock"
        if inventory not in (None, "")
        else "inventory unknown"
    )
    sku = product.get("sku") or "no SKU"
    return (
        f"{index}. {product.get('name') or 'Unnamed product'} "
        f"(ID {product.get('id')}, SKU {sku}) | "
        f"{_format_catalog_price(product.get('price'))} | "
        f"{visible} | {product.get('availability') or 'availability unknown'} | "
        f"{inventory_label} | {product.get('variant_count') or 0} variants | "
        f"{product.get('image_count') or 0} images"
    )


def _format_catalog_products(result: dict[str, Any]) -> str:
    products = result.get("products") or []
    if not products:
        return "I did not find matching catalog products."

    source_label = "cached" if "cache_status" in result else "live"
    lines = [f"Found {len(products)} {source_label} catalog product{'' if len(products) == 1 else 's'}:"]
    for index, product in enumerate(products[:20], start=1):
        lines.append(_format_catalog_product_line(product, index))

    pagination = result.get("pagination") or {}
    total = pagination.get("total")
    if total and int(total) > len(products):
        lines.append(f"Showing {len(products)} of {total} matching catalog products.")

    return "\n".join(lines)


def _format_catalog_product_profile(result: dict[str, Any]) -> str:
    product = result.get("product") or {}
    if not product:
        return "I did not find that product in the cached catalog."

    sales = result.get("sales_summary") or {}
    lines = [
        f"{product.get('name') or 'Unnamed product'}",
        f"- SKU: {product.get('sku') or 'unknown'}",
        f"- Price: {_format_catalog_price(product.get('price'))}",
        f"- Availability: {product.get('availability') or 'unknown'}",
        f"- Visible on site: {'Yes' if product.get('is_visible') else 'No'}",
        f"- Inventory: {_format_number(product.get('inventory_level')) if product.get('inventory_level') is not None else 'unknown'}",
    ]
    if product.get("manufacturer"):
        lines.append(f"- Manufacturer: {product.get('manufacturer')}")
    if product.get("cpu_family"):
        lines.append(f"- CPU family: {product.get('cpu_family')}")
    if product.get("product_kind"):
        lines.append(f"- Product kind: {product.get('product_kind')}")
    if product.get("custom_url"):
        lines.append(f"- Product page: {product.get('custom_url')}")
    variants = product.get("variants") or []
    if variants:
        lines.append(f"- Variants: {len(variants)}")

    lines.extend(
        [
            "",
            "Sales history from the local order cache:",
            (
                f"- {_format_number(sales.get('quantity_sold') or 0)} units across "
                f"{_format_number(sales.get('order_count') or 0)} orders"
            ),
            f"- Revenue: {_format_money(float(sales.get('revenue_inc_tax') or 0))}",
        ]
    )
    if sales.get("first_order_date") or sales.get("last_order_date"):
        lines.append(
            f"- First/most recent order: {sales.get('first_order_date') or 'unknown'} / "
            f"{sales.get('last_order_date') or 'unknown'}"
        )
    return "\n".join(lines)


def _format_catalog_filtered_product_sales(result: dict[str, Any]) -> str:
    products = result.get("products") or []
    months = result.get("months") or []
    terms = [result.get("keyword"), *(result.get("required_terms") or [])]
    label = ", ".join(str(term) for term in terms if term)
    range_label = ""
    if result.get("start_date") or result.get("end_date"):
        range_label = f" from {result.get('start_date') or 'the beginning'} through {result.get('end_date') or 'now'}"

    if result.get("group_by") == "month":
        if not months:
            catalog_count = result.get("catalog_match_count") or 0
            if catalog_count:
                return (
                    f"I found {catalog_count} catalog products matching {label or 'that filter'}, "
                    "but none had matching sales in the local order cache for that date range."
                )
            return f"I did not find live catalog products matching {label or 'that filter'}."

        lines = [
            f"Monthly sales for catalog products matching {label or 'that filter'}{range_label}:",
            "",
            "| Month | Units | Revenue | Orders |",
            "|---|---:|---:|---:|",
        ]
        for month in months:
            lines.append(
                (
                    f"| {month.get('month') or 'unknown'} | "
                    f"{_format_number(month.get('quantity_sold') or 0)} | "
                    f"{_format_money(float(month.get('revenue_inc_tax') or 0))} | "
                    f"{_format_number(month.get('order_count') or 0)} |"
                )
            )
        lines.extend(
            [
                "",
                (
                    f"Total: {_format_number(result.get('total_quantity_sold') or 0)} units, "
                    f"{_format_money(float(result.get('total_revenue_inc_tax') or 0))}."
                ),
            ]
        )
        return "\n".join(lines)

    if not products:
        catalog_count = result.get("catalog_match_count") or 0
        if catalog_count:
            return (
                f"I found {catalog_count} catalog products matching {label or 'that filter'}, "
                "but none had matching sales in the local order cache."
            )
        return f"I did not find live catalog products matching {label or 'that filter'}."

    lines = [
        (
            f"Best-selling catalog products matching {label or 'that filter'}{range_label}, "
            "ranked by units sold:"
        )
    ]
    for index, product in enumerate(products[:10], start=1):
        catalog_product = product.get("catalog_product") or {}
        price = _format_catalog_price(catalog_product.get("price"))
        lines.append(
            (
                f"{index}. {product.get('name') or catalog_product.get('name') or 'Unknown product'} "
                f"(SKU {product.get('sku') or catalog_product.get('sku') or 'unknown'}, "
                f"ID {product.get('product_id') or catalog_product.get('id') or 'unknown'}) | "
                f"{_format_number(product.get('quantity_sold') or 0)} units | "
                f"{_format_money(float(product.get('revenue_inc_tax') or 0))} | "
                f"{product.get('order_count') or 0} orders | catalog price {price}"
            )
        )

    lines.append(
        (
            f"Totals for returned rows: {_format_number(result.get('total_quantity_sold') or 0)} units, "
            f"{_format_money(float(result.get('total_revenue_inc_tax') or 0))}."
        )
    )
    return "\n".join(lines)


def _format_cpu_family_sales_breakdown(result: dict[str, Any]) -> str:
    if not result.get("is_reliable_for_cpu_family"):
        return (
            "I can't reliably answer CPU-family sales questions yet because the local catalog cache has "
            "0 products. CPU-family classification needs catalog/spec data; order-line text alone can "
            "miss most machines or misclassify graphics as CPUs. Run a catalog/cache sync first, then ask again."
        )

    periods = result.get("periods") or []
    families = result.get("cpu_families") or ["Windows ARM", "Apple silicon", "Intel", "AMD"]
    range_label = f"{result.get('start_date') or 'the beginning'} through {result.get('end_date') or 'now'}"
    if not periods:
        return f"I did not find classified machine sales by CPU family for {range_label}."

    lines = [
        f"CPU-family machine purchase mix for {range_label}:",
        "",
        "| Period | Total units | " + " | ".join(str(family) for family in families) + " |",
        "|---|---:|" + "|".join("---:" for _ in families) + "|",
    ]
    for period in periods:
        family_counts = period.get("cpu_families") or {}
        cells = []
        for family in families:
            values = family_counts.get(family) or {}
            cells.append(
                (
                    f"{_format_number(values.get('quantity') or 0)} "
                    f"({float(values.get('percentage') or 0):.2f}%)"
                )
            )
        lines.append(
            (
                f"| {period.get('period') or 'unknown'} | "
                f"{_format_number(period.get('total_classified_quantity') or 0)} | "
                + " | ".join(cells)
                + " |"
            )
        )

    overall = result.get("overall") or {}
    overall_counts = overall.get("cpu_families") or {}
    summary_parts = []
    for family in families:
        values = overall_counts.get(family) or {}
        summary_parts.append(
            (
                f"{family}: {_format_number(values.get('quantity') or 0)} "
                f"({float(values.get('percentage') or 0):.2f}%)"
            )
        )
    lines.extend(
        [
            "",
            (
                f"Overall classified units: "
                f"{_format_number(overall.get('total_classified_quantity') or 0)}."
            ),
            "; ".join(summary_parts) + ".",
        ]
    )

    contributor_lines = []
    for family in families:
        top_products = (overall_counts.get(family) or {}).get("top_products") or []
        if not top_products:
            continue
        product_bits = [
            f"{product.get('name') or 'Unknown'} ({_format_number(product.get('quantity') or 0)})"
            for product in top_products[:3]
        ]
        contributor_lines.append(f"- {family}: " + "; ".join(product_bits))
    if contributor_lines:
        lines.extend(["", "Top product contributors:", *contributor_lines])

    unclassified_quantity = int(result.get("unclassified_quantity") or 0)
    if unclassified_quantity:
        lines.append(
            (
                f"Unclassified line-item units in the same date range: "
                f"{_format_number(unclassified_quantity)}."
            )
        )
    return "\n".join(lines)


def _format_order_financial_summary(result: dict[str, Any]) -> str:
    range_label = "all time"
    if result.get("start_date") or result.get("end_date"):
        range_label = f"{result.get('start_date') or 'the beginning'} through {result.get('end_date') or 'now'}"
    lines = [
        f"Financial summary for {range_label}:",
        f"- Sales: {_format_money(float(result.get('total_sales_inc_tax') or 0))}",
        f"- Orders: {_format_number(result.get('order_count') or 0)}",
        f"- Items: {_format_number(result.get('item_quantity') or 0)}",
        f"- Subtotal: {_format_money(float(result.get('subtotal_inc_tax') or 0))}",
        f"- Shipping charged: {_format_money(float(result.get('shipping_inc_tax') or 0))}",
    ]
    if result.get("savings_available"):
        lines.insert(
            2,
            f"- Known savings/discounts: {_format_money(float(result.get('known_savings_total') or 0))}",
        )
    else:
        lines.extend(
            [
                "",
                "Savings/discounts: unknown from the available BigCommerce cache. I do not see explicit discount/coupon amount fields for this range, so a savings definition or another data source would be needed.",
            ]
        )
    return "\n".join(lines)


def _plural(count: Any, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def _order_url(order_id: Any) -> str:
    return f"{ORDER_ADMIN_BASE_URL}/{int(order_id)}"


def _extract_relevant_order_ids(text: str) -> list[int]:
    seen: set[int] = set()
    order_ids: list[int] = []

    def add(value: str) -> None:
        order_id = int(value)
        if order_id not in seen:
            seen.add(order_id)
            order_ids.append(order_id)

    for match in re.finditer(r"\bOrder\s+#?(\d{1,})\b", text, re.IGNORECASE):
        add(match.group(1))

    for match in re.finditer(
        r"\bOrder IDs?(?:\s+include|:)?\s+([0-9,\sand]+)",
        text,
        re.IGNORECASE,
    ):
        for order_id in re.findall(r"\d{1,}", match.group(1)):
            add(order_id)

    for match in re.finditer(
        r"\border(?:s)?\s+((?:\d{1,}(?:\s*,\s*|\s+and\s+)?)+)",
        text,
        re.IGNORECASE,
    ):
        for order_id in re.findall(r"\d{1,}", match.group(1)):
            add(order_id)

    return order_ids


def _add_order_links(answer: str) -> str:
    return answer


def _format_purchase_breakdown(result: dict[str, Any]) -> str:
    units = result.get("units") or result.get("groups") or []
    displayed = result.get("displayed_totals", {})
    remaining = result.get("remaining_totals", {})
    group_label = result.get("group_label") or result.get("dimension_label") or "college/unit"

    lines = [
        (
            f"Here is the {group_label} purchase breakdown for the last {result.get('days')} days, "
            "ranked by revenue including tax."
        ),
        "",
        (
            f"Displayed: top {result.get('displayed_unit_count') or result.get('displayed_group_count')} of "
            f"{result.get('total_unit_count') or result.get('total_group_count')} {group_label} groups. "
            f"These displayed groups cover {displayed.get('revenue_percentage')}% of revenue, "
            f"{displayed.get('order_percentage')}% of orders "
            f"({displayed.get('order_count')} of {result.get('total_orders')}), and "
            f"{displayed.get('item_quantity_percentage')}% of item quantity."
        ),
    ]

    if remaining.get("unit_count", 0):
        lines.append(
            (
                f"All other groups: {remaining.get('unit_count') or remaining.get('group_count')} groups, "
                f"{remaining.get('revenue_percentage')}% of revenue, "
                f"{remaining.get('order_percentage')}% of orders "
                f"({remaining.get('order_count')} orders), and "
                f"{remaining.get('item_quantity_percentage')}% of item quantity."
            )
        )

    lines.extend(["", f"Top {group_label} groups:"])
    for index, unit in enumerate(units, start=1):
        lines.append(
            (
                f"{index}. {unit['group']}: "
                f"{unit['revenue_percentage']}% revenue "
                f"({_format_money(unit['total_inc_tax'])}), "
                f"{unit['order_count']} {_plural(unit['order_count'], 'order')} "
                f"({unit['order_percentage']}% of orders), "
                f"{_format_number(unit['item_quantity'])} {_plural(unit['item_quantity'], 'item')} "
                f"({unit['item_quantity_percentage']}% of items)"
            )
        )

    if result.get("unknown_unit_order_count"):
        lines.extend(
            [
                "",
                (
                    f"Note: {result['unknown_unit_order_count']} orders did not have a readable "
                    "college/unit value."
                ),
            ]
        )

    return "\n".join(lines)


def _format_revenue_summary(result: dict[str, Any]) -> str:
    end_label = result.get("end_date") or "now"
    lines = [
        (
            f"Total revenue from {result.get('start_date')} through {end_label}: "
            f"{_format_money(result.get('total_revenue_inc_tax') or 0)}"
        ),
        (
            f"Basis: {result.get('metric_basis')}; "
            f"{result.get('included_order_count')} included orders "
            f"out of {result.get('orders_analyzed')} analyzed."
        ),
        (
            f"Subtotal including tax: {_format_money(result.get('subtotal_inc_tax') or 0)}; "
            f"tax total: {_format_money(result.get('tax_total') or 0)}; "
            f"shipping including tax: {_format_money(result.get('shipping_total_inc_tax') or 0)}."
        ),
    ]

    refunded = result.get("refunded_amount_total") or 0
    if refunded:
        lines.append(f"Refunded amount total reported on orders: {_format_money(refunded)}.")

    if result.get("excluded_order_count"):
        lines.append(
            (
                f"Excluded {result.get('excluded_order_count')} orders with statuses "
                f"{', '.join(result.get('excluded_statuses') or [])}; "
                f"their order totals sum to {_format_money(result.get('excluded_status_total_inc_tax') or 0)}."
            )
        )

    if result.get("is_truncated"):
        lines.append(
            (
                f"Warning: this hit the max_orders limit of {result.get('max_orders')}; "
                "the total may be incomplete."
            )
        )

    status_counts = result.get("included_status_counts") or {}
    if status_counts:
        statuses = ", ".join(f"{status}: {count}" for status, count in status_counts.items())
        lines.append(f"Included order statuses: {statuses}.")

    return "\n".join(lines)


def _date_range_label(result: dict[str, Any]) -> str:
    if result.get("start_date"):
        return f"{result.get('start_date')} through {result.get('end_date') or 'now'}"
    return f"the last {result.get('days')} days"


def _format_shipping_spend(result: dict[str, Any]) -> str:
    keyword = result.get("method_keyword")
    range_label = _date_range_label(result)

    if keyword:
        lines = [
            (
                f"Customer shipping charges for methods matching {keyword!r} for {range_label}: "
                f"{_format_money(result.get('matched_shipping_total_inc_tax') or 0)}"
            ),
            (
                f"Matched {result.get('matched_order_count')} orders and "
                f"{result.get('matched_shipping_address_count')} shipping addresses."
            ),
        ]
    else:
        lines = [
            (
                f"Customer shipping charges by method for {range_label} "
                f"({result.get('shipping_address_orders_scanned')} orders with shipping cost scanned):"
            )
        ]

    methods = result.get("top_methods") or []
    if methods:
        lines.extend(["", "Matching shipping methods:"])
        for item in methods:
            lines.append(
                (
                    f"- {item.get('shipping_method')}: "
                    f"{_format_money(item.get('shipping_total_inc_tax') or 0)} "
                    f"across {item.get('address_count')} addresses"
                )
            )

    carriers = result.get("matched_carriers") or []
    if carriers:
        lines.extend(["", "Matching shipment carriers/providers:"])
        for item in carriers:
            lines.append(
                (
                    f"- {item.get('carrier_or_provider')}: "
                    f"{_format_money(item.get('shipping_total_inc_tax') or 0)} "
                    f"across {item.get('shipment_count')} shipments"
                )
            )
    if keyword and not methods and not carriers:
        lines.append("")
        lines.append(
            f"No shipping address methods or shipment carrier/provider fields containing "
            f"{keyword!r} were found in the scanned records."
        )

    lines.append("")
    lines.append(
        "Important limitation: this is what customers were charged on BigCommerce orders, "
        "not what your team paid the carrier. Actual carrier invoice spend is not available "
        "from the current BigCommerce order API data."
    )
    lines.append(f"Basis: {result.get('metric_basis')}.")
    lines.append(
        (
            f"Scanned {result.get('shipping_address_orders_scanned')} of "
            f"{result.get('shipping_cost_order_count')} non-zero-shipping orders "
            f"from {result.get('included_order_count')} included orders "
            f"({result.get('orders_analyzed')} analyzed)."
        )
    )

    if result.get("shipping_address_scan_truncated"):
        lines.append(
            (
                f"Warning: shipping-address scan hit the limit of "
                f"{result.get('shipping_address_scan_limit')} orders, so this may be incomplete."
            )
        )
    if result.get("is_truncated"):
        lines.append(
            (
                f"Warning: order scan hit max_orders={result.get('max_orders')}; "
                "older orders may be missing."
            )
        )

    return "\n".join(lines)


def _format_order_summary(result: dict[str, Any]) -> str:
    lines = [
        f"Order summary for {_date_range_label(result)}:",
        (
            f"- Revenue: {_format_money(result.get('total_revenue_inc_tax') or 0)} "
            f"across {result.get('matching_order_count')} orders"
        ),
        f"- Items: {_format_number(result.get('item_quantity') or 0)}",
        f"- Average order value: {_format_money(result.get('average_order_value_inc_tax') or 0)}",
    ]

    if result.get("matching_product_quantity") is not None:
        lines.append(
            (
                f"- Matching product revenue: "
                f"{_format_money(result.get('matching_product_revenue_inc_tax') or 0)} "
                f"for {_format_number(result.get('matching_product_quantity') or 0)} units"
            )
        )

    top_products = result.get("top_matching_products") or []
    if top_products:
        lines.append("- Top matching products:")
        for product in top_products[:5]:
            lines.append(f"  - {product['name']}: {_format_number(product['quantity_sold'])}")

    statuses = result.get("status_counts") or {}
    if statuses:
        lines.append(
            "- Statuses: "
            + ", ".join(f"{status}: {count}" for status, count in statuses.items())
        )

    if result.get("is_truncated"):
        lines.append(
            f"Warning: this hit max_orders={result.get('max_orders')}; totals may be incomplete."
        )
    if result.get("line_item_scan_truncated"):
        lines.append(
            (
                f"Warning: line-item scan was capped at "
                f"{result.get('line_item_orders_scanned')} orders "
                f"(limit {result.get('line_item_scan_limit')}); product metrics may be incomplete."
            )
        )

    return "\n".join(lines)


def _format_grouped_order_summary(result: dict[str, Any]) -> str:
    lines = [
        (
            f"Grouped order summary by {result.get('group_by')} for "
            f"{_date_range_label(result)}:"
        ),
        (
            f"Total: {_format_money(result.get('total_revenue_inc_tax') or 0)} across "
            f"{result.get('matching_order_count')} orders and "
            f"{_format_number(result.get('total_item_quantity') or 0)} items."
        ),
        (
            f"Displaying {result.get('displayed_group_count')} of "
            f"{result.get('total_group_count')} groups. Basis: {result.get('metric_basis')}; "
            f"sorted by {result.get('sort_by') or 'revenue'}."
        ),
        "",
    ]

    for index, group in enumerate(result.get("groups") or [], start=1):
        lines.append(
            (
                f"{index}. {group['group']}: "
                f"{_format_money(group['total_inc_tax'])} "
                f"({group['revenue_percentage']}% revenue), "
                f"{group['order_count']} orders, "
                f"{_format_number(group['item_quantity'])} items"
            )
        )

    if result.get("is_truncated"):
        lines.append(
            f"Warning: this hit max_orders={result.get('max_orders')}; totals may be incomplete."
        )
    if result.get("line_item_scan_truncated"):
        lines.append(
            (
                f"Warning: line-item scan was capped at "
                f"{result.get('line_item_orders_scanned')} orders "
                f"(limit {result.get('line_item_scan_limit')}); product/group metrics may be incomplete."
            )
        )

    return "\n".join(lines)


def _format_ranked_orders(result: dict[str, Any]) -> str:
    orders = result.get("orders") or []
    direction = result.get("direction") or "desc"
    sort_by = result.get("sort_by") or "total_inc_tax"
    labels = {
        ("total_inc_tax", "desc"): "Highest-value",
        ("total_inc_tax", "asc"): "Lowest-value",
        ("date_created", "asc"): "Earliest submitted",
        ("date_created", "desc"): "Latest submitted",
        ("order_id", "asc"): "Lowest order-number",
        ("order_id", "desc"): "Highest order-number",
        ("items_total", "desc"): "Most-item",
        ("items_total", "asc"): "Fewest-item",
    }
    label = labels.get((sort_by, direction), "Ranked")
    lines = [
        (
            f"{label} orders for {_date_range_label(result)} "
            f"({result.get('matching_order_count')} matching orders):"
        )
    ]

    if not orders:
        return "\n".join(lines + ["No matching orders found."])

    for index, order in enumerate(orders, start=1):
        item_count = int(order.get("items_total") or 0)
        lines.append(
            (
                f"{index}. Order {order.get('order_id')} | "
                f"{_format_money(float(order.get('total_inc_tax') or 0))} | "
                f"{_format_number(item_count)} {_plural(item_count, 'item')} | "
                f"{order.get('status')} | placed by {order.get('placed_by')} | "
                f"{order.get('college_unit')} | {order.get('date_created')}"
            )
        )

    if result.get("is_truncated"):
        lines.append(
            f"Warning: order scan hit max_orders={result.get('max_orders')}; ranking may be incomplete."
        )

    return "\n".join(lines)


def _format_product_sales_leaderboard(result: dict[str, Any]) -> str:
    products = result.get("products") or []
    if not products:
        return f"I did not find matching product sales for {_date_range_label(result)}."

    top = products[0]
    top_order = top.get("top_order") or {}
    lines = [
        (
            f"Top matching product for {_date_range_label(result)}: "
            f"{top.get('product_name')} with "
            f"{_format_number(top.get('quantity_sold') or 0)} units sold."
        ),
        (
            f"Product revenue: {_format_money(float(top.get('total_inc_tax') or 0))} "
            f"across {top.get('order_count')} {_plural(top.get('order_count') or 0, 'order')}."
        ),
    ]

    if top_order:
        lines.extend(
            [
                "",
                (
                    f"Order with the most of that product: Order {top_order.get('order_id')} "
                    f"with {_format_number(top_order.get('matching_quantity') or 0)} units."
                ),
                (
                    f"Placed by {top_order.get('placed_by') or 'Unknown'} | "
                    f"{top_order.get('college_unit') or 'Unknown college/unit'} | "
                    f"{top_order.get('status') or 'Unknown status'} | "
                    f"matching line total "
                    f"{_format_money(float(top_order.get('matching_line_total_inc_tax') or 0))}."
                ),
            ]
        )

    if len(products) > 1:
        lines.extend(["", "Next highest products:"])
        for index, product in enumerate(products[1:5], start=2):
            lines.append(
                (
                    f"{index}. {product.get('product_name')}: "
                    f"{_format_number(product.get('quantity_sold') or 0)} units, "
                    f"{_format_money(float(product.get('total_inc_tax') or 0))}"
                )
            )

    if result.get("line_item_scan_truncated"):
        lines.append(
            (
                f"Warning: line-item scan was capped at "
                f"{result.get('line_item_orders_scanned')} orders "
                f"(limit {result.get('line_item_scan_limit')}); this can miss older orders."
            )
        )
    if result.get("is_truncated"):
        lines.append(
            f"Warning: order scan hit max_orders={result.get('max_orders')}; totals may be incomplete."
        )

    return "\n".join(lines)


def _format_source_orders(result: dict[str, Any]) -> str:
    lines = [
        (
            f"Source orders for {_date_range_label(result)}: "
            f"{result.get('returned_count')} of {result.get('matching_order_count')} returned."
        )
    ]

    for order in result.get("orders") or []:
        lines.append(
            (
                f"- Order {order.get('order_id')} | "
                f"{order.get('date_created')} | {order.get('status')} | "
                f"placed by {order.get('placed_by')} | {order.get('college_unit')} | "
                f"total {order.get('total_inc_tax')}"
            )
        )
        for product in order.get("matching_products") or []:
            lines.append(
                (
                    f"  - {product.get('quantity')} x {product.get('name')} "
                    f"[SKU: {product.get('sku') or 'N/A'}] "
                    f"line total: {product.get('total_inc_tax')}"
                )
            )

    if result.get("is_truncated"):
        lines.append(
            f"Warning: this hit max_orders={result.get('max_orders')}; source set may be incomplete."
        )
    if result.get("line_item_scan_truncated"):
        lines.append(
            (
                f"Warning: line-item source scan was capped at "
                f"{result.get('line_item_orders_scanned')} orders "
                f"(limit {result.get('line_item_scan_limit')})."
            )
        )

    return "\n".join(lines)


def _format_age(age: dict[str, Any] | None) -> str:
    if not age:
        return "Unknown"
    days = float(age.get("days") or 0)
    hours = float(age.get("hours") or 0)
    if days >= 1:
        return f"{days:.2f} days"
    return f"{hours:.2f} hours"


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    current = date(year, month, 1)
    days_until_weekday = (weekday - current.weekday()) % 7
    return current + timedelta(days=days_until_weekday + (n - 1) * 7)


def _central_timezone_for_utc(value: datetime) -> timezone:
    year = value.year
    dst_start_day = _nth_weekday(year, 3, 6, 2)
    dst_end_day = _nth_weekday(year, 11, 6, 1)
    dst_start_utc = datetime(year, 3, dst_start_day.day, 8, tzinfo=timezone.utc)
    dst_end_utc = datetime(year, 11, dst_end_day.day, 7, tzinfo=timezone.utc)
    if dst_start_utc <= value < dst_end_utc:
        return timezone(timedelta(hours=-5), "CDT")
    return timezone(timedelta(hours=-6), "CST")


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = parsedate_to_datetime(str(value))
        except (TypeError, ValueError):
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed_utc = parsed.astimezone(timezone.utc)
    if DISPLAY_TIMEZONE is None:
        return parsed_utc.astimezone(_central_timezone_for_utc(parsed_utc))
    return parsed_utc.astimezone(DISPLAY_TIMEZONE)


def _format_timestamp(value: Any) -> str:
    parsed = _parse_timestamp(value)
    if not parsed:
        return str(value) if value else "Unknown"
    hour = parsed.hour % 12 or 12
    am_pm = "AM" if parsed.hour < 12 else "PM"
    zone = parsed.tzname() or "UTC"
    return f"{parsed:%b} {parsed.day}, {parsed:%Y} {hour}:{parsed:%M} {am_pm} {zone}"


def _format_oldest_unfulfilled(result: dict[str, Any]) -> str:
    lines = [
        (
            f"Oldest currently unfulfilled orders from the last {result.get('days')} days "
            f"({result.get('open_order_count')} open orders found):"
        )
    ]

    for index, order in enumerate(result.get("orders") or [], start=1):
        lines.append(
            (
                f"{index}. Order {order.get('order_id')} | "
                f"{order.get('status')} | age {order.get('age_days')} days "
                f"({order.get('age_hours')} hours) | placed by {order.get('placed_by')} | "
                f"{order.get('college_unit')} | {order.get('items_total')} items"
            )
        )

    common_items = result.get("common_items_on_returned_orders") or []
    if common_items:
        lines.extend(["", "Most common items on these returned orders:"])
        for item in common_items:
            lines.append(
                f"- {item['name']}: {_format_number(item['quantity'])} units"
            )

    if result.get("is_truncated"):
        lines.append(
            f"Warning: this hit max_orders={result.get('max_orders')}; results may be incomplete."
        )

    return "\n".join(lines)


def _format_fulfillment_aging_report(result: dict[str, Any]) -> str:
    lines = [
        (
            f"Fulfillment aging report for the last {result.get('days')} days "
            f"({result.get('orders_analyzed')} orders analyzed):"
        ),
        "",
        (
            f"Longest completed fulfillment durations "
            f"({result.get('fulfilled_order_count_with_timing')} fulfilled orders with timing):"
        ),
    ]

    for index, order in enumerate(result.get("longest_fulfilled_orders") or [], start=1):
        lines.append(
            (
                f"{index}. Order {order.get('order_id')} | "
                f"{order.get('status')} | took {order.get('days')} days "
                f"({order.get('hours')} hours) | placed by {order.get('placed_by')} | "
                f"{order.get('college_unit')} | {order.get('items_total')} items"
            )
        )

    lines.extend(
        [
            "",
            (
                f"Oldest currently unfulfilled orders "
                f"({result.get('open_order_count')} open orders found):"
            ),
        ]
    )
    for index, order in enumerate(result.get("oldest_open_orders") or [], start=1):
        lines.append(
            (
                f"{index}. Order {order.get('order_id')} | "
                f"{order.get('status')} | open {order.get('days')} days "
                f"({order.get('hours')} hours) | placed by {order.get('placed_by')} | "
                f"{order.get('college_unit')} | {order.get('items_total')} items"
            )
        )

    common_items = result.get("common_items_on_returned_orders") or []
    if common_items:
        lines.extend(["", "Most common items across the returned orders above:"])
        for item in common_items:
            lines.append(f"- {item['name']}: {_format_number(item['quantity'])} units")

    if result.get("is_truncated"):
        lines.append(
            f"Warning: this hit max_orders={result.get('max_orders')}; results may be incomplete."
        )

    return "\n".join(lines)


def _format_fulfillment_timing(result: dict[str, Any]) -> str:
    order_id = result.get("order_id")
    if result.get("fulfillment_duration"):
        duration = _format_age(result.get("fulfillment_duration"))
        lines = [
            (
                f"Order {order_id} took {duration} "
                "from order creation to first shipment."
            ),
            f"Status: {result.get('status')}. Basis: {result.get('timing_basis')}.",
            f"Created: {_format_timestamp(result.get('created_at') or result.get('date_created'))}.",
        ]
        if result.get("first_shipment_at"):
            lines.append(f"First shipped: {_format_timestamp(result.get('first_shipment_at'))}.")
        if result.get("last_modified_at"):
            lines.append(
                f"Last modified/completed: {_format_timestamp(result.get('last_modified_at'))}."
            )
        if result.get("status_timeline_note"):
            lines.append(
                "Important limitation: I can measure created-to-shipped timing, "
                "but I cannot break that into exact status-stage dwell times from "
                "the current API data."
            )
        return "\n".join(lines)

    if result.get("age_if_unfulfilled"):
        age = _format_age(result.get("age_if_unfulfilled"))
        lines = [
            (
                f"Order {order_id} is not fulfilled yet. "
                f"It has been open for {age} since it was created on "
                f"{_format_timestamp(result.get('created_at') or result.get('date_created'))}. "
                f"Status: {result.get('status')}."
            )
        ]
        if result.get("status_timeline_note"):
            lines.append(
                "Important limitation: I can measure age since creation, but I "
                "cannot see exact status-stage dwell times from the current API data."
            )
        return "\n".join(lines)

    return (
        f"Order {order_id} has status {result.get('status')}, "
        "but I could not determine a fulfillment duration from the available timestamps."
    )


def _format_order_contents(result: dict[str, Any]) -> str:
    orders = result.get("orders", [])
    lines = [
        (
            f"Full order contents for {len(orders)} matching "
            f"{_plural(len(orders), 'order')}:"
        )
    ]

    for item in orders:
        order = item.get("order", {})
        products = item.get("products", [])
        lines.extend(
            [
                "",
                (
                    f"Order {order.get('id')} | "
                    f"{order.get('date_created')} | "
                    f"{order.get('status')} | placed by {order.get('placed_by') or order.get('customer')} | "
                    f"billing {order.get('billing_contact') or 'Unknown'} | "
                    f"{order.get('college_unit')} | Total {order.get('total_inc_tax')}"
                ),
            ]
        )
        recipient = order.get("recipient") or {}
        if recipient:
            account_numbers = recipient.get("account_numbers") or []
            recipient_names = recipient.get("recipient_names_or_uins") or []
            lines.append(
                (
                    "Recipient fields: "
                    f"college/unit {recipient.get('college_unit') or 'Unknown'}; "
                    f"department code {recipient.get('department_code') or 'Unknown'}; "
                    f"account {', '.join(account_numbers) if account_numbers else 'Unknown'}; "
                    f"recipient/name/UIN {', '.join(recipient_names) if recipient_names else 'Unknown'}"
                )
            )
        shipping_addresses = order.get("shipping_addresses") or []
        if shipping_addresses:
            lines.append("Shipping:")
            for index, address in enumerate(shipping_addresses, start=1):
                lines.append(f"  {index}. {_format_address(address)}")
        for product in products:
            quantity = product.get("quantity", 0)
            lines.append(
                (
                    f"- {quantity} x {product.get('name')} "
                    f"[SKU: {product.get('sku') or 'N/A'}] "
                    f"line total: {product.get('total_inc_tax')}"
                )
            )

    contact_only = result.get("billing_contact_only_examples") or []
    if contact_only:
        lines.extend(
            [
                "",
                (
                    "Separate note: the name also appears as a billing/shipping contact "
                    "on these orders, but the BigCommerce placed-by customer account is different:"
                ),
            ]
        )
        for order in contact_only:
            lines.append(
                (
                    f"- Order {order.get('order_id')} | placed by {order.get('placed_by')} | "
                    f"billing {order.get('billing_contact')} | {order.get('college_unit')} | "
                    f"Total {order.get('total_inc_tax')}"
                )
            )

    return "\n".join(lines)


def _format_address(address: dict[str, Any] | None) -> str:
    if not address:
        return "Unknown"

    parts = [
        address.get("name"),
        address.get("company"),
        address.get("street"),
        ", ".join(
            str(value)
            for value in [address.get("city"), address.get("state"), address.get("zip")]
            if value
        )
        or None,
        address.get("country"),
        address.get("email"),
        address.get("phone"),
    ]
    return " | ".join(str(part) for part in parts if part) or "Unknown"


def _format_order_identity(result: dict[str, Any]) -> str:
    placed_by = result.get("placed_by") or {}
    recipient = result.get("recipient") or {}
    shipping_addresses = result.get("shipping_addresses") or []
    account_numbers = recipient.get("account_numbers") or []
    recipient_names = recipient.get("recipient_names_or_uins") or []
    placed_by_suffix = ""
    if placed_by.get("email"):
        placed_by_suffix += f" <{placed_by.get('email')}>"
    if placed_by.get("id"):
        placed_by_suffix += f" (customer ID {placed_by.get('id')})"

    lines = [
        f"Order {result.get('order_id')} identity:",
        f"- Placed by: {placed_by.get('name') or 'Unknown'}{placed_by_suffix}",
        f"- Billing/contact: {_format_address(result.get('billing_contact'))}",
        (
            "- Recipient checkout fields: "
            f"college/unit {recipient.get('college_unit') or 'Unknown'}; "
            f"department code {recipient.get('department_code') or 'Unknown'}; "
            f"account {', '.join(account_numbers) if account_numbers else 'Unknown'}; "
            f"recipient/name/UIN {', '.join(recipient_names) if recipient_names else 'Unknown'}"
        ),
    ]

    if shipping_addresses:
        lines.append("- Shipping:")
        for index, address in enumerate(shipping_addresses, start=1):
            lines.append(f"  {index}. {_format_address(address)}")
    else:
        lines.append("- Shipping: Unknown")

    lines.extend(
        [
            f"- Status: {result.get('status') or 'Unknown'}",
            f"- Total: {result.get('total_inc_tax') or 'Unknown'}",
            "",
            "Placed-by is the BigCommerce customer account on the order; shipping and recipient fields can refer to someone else.",
        ]
    )
    return "\n".join(lines)


def _assistant_history_message(message: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content") or "",
    }
    if message.get("tool_calls"):
        clean["tool_calls"] = message["tool_calls"]
    return clean


def _extract_json_object(text_value: str, start_index: int) -> str | None:
    decoder = json.JSONDecoder()
    for index in range(start_index, len(text_value)):
        if text_value[index] != "{":
            continue
        try:
            _, end = decoder.raw_decode(text_value[index:])
        except json.JSONDecodeError:
            continue
        return text_value[index : index + end]
    return None


def _coerce_text_tool_call(
    message: dict[str, Any],
    allowed_tool_names: set[str] | None = None,
) -> dict[str, Any]:
    """Handle local OpenAI-compatible servers that emit tool calls as text."""

    if message.get("tool_calls"):
        return message

    content = message.get("content")
    if not isinstance(content, str):
        return message

    match = re.search(r"\bto=functions\.([A-Za-z_][\w]*)\b", content)
    if not match:
        return message

    name = match.group(1)
    allowed = allowed_tool_names or set(CHAT_TOOLS)
    if name not in allowed or name not in CHAT_TOOLS:
        return message

    arguments = _extract_json_object(content, match.end())
    if not arguments:
        return message

    coerced = dict(message)
    coerced["content"] = ""
    coerced["tool_calls"] = [
        {
            "id": f"text_tool_call_{name}",
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }
    ]
    return coerced


def _sanitize_assistant_answer(answer: str) -> str:
    cleaned = re.sub(
        r"\bto=functions\.[A-Za-z_][\w]*\b.*?(?=\n\n|$)",
        "",
        answer,
        flags=re.DOTALL,
    )
    return cleaned.strip()


def _should_keep_cache_freshness_note(question: str) -> bool:
    normalized = question.lower()
    freshness_terms = [
        "sync",
        "synced",
        "stale",
        "fresh",
        "current data",
        "up to date",
        "up-to-date",
        "right now",
        "live",
        "today",
        "today's",
        "todays",
        "latest",
        "newest",
        "recent orders",
        "since last sync",
        "since the last sync",
    ]
    return any(term in normalized for term in freshness_terms)


def _remove_unneeded_cache_freshness_note(answer: str, question: str) -> str:
    if _should_keep_cache_freshness_note(question):
        return answer

    cleaned = re.sub(
        r"\n{0,2}Note:\s*(?:the\s+)?(?:local\s+)?cache\b.*?(?:last successful sync|last synced|last sync|completed on).*?(?=\n\n|$)",
        "",
        answer,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(
        r"\n{0,2}(?:The\s+)?(?:local\s+)?cache\s+(?:also\s+)?(?:appears\s+)?(?:slightly\s+)?stale\b.*?(?=\n\n|$)",
        "",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return cleaned.strip()


def _prepare_final_answer(answer: str, question: str) -> str:
    return _add_order_links(
        _remove_unneeded_cache_freshness_note(
            _sanitize_assistant_answer(answer),
            question,
        )
    )


def _extract_order_ids_from_history(messages: list[dict[str, Any]] | None) -> list[int]:
    seen: set[int] = set()
    order_ids: list[int] = []

    def add(order_id: int) -> None:
        if order_id not in seen:
            seen.add(order_id)
            order_ids.append(order_id)

    def scan(content: str) -> None:
        for match in re.finditer(r"\bOrder\s+(\d{1,})\b", content, re.IGNORECASE):
            add(int(match.group(1)))

        for match in re.finditer(
            r"\bOrder IDs?(?:\s+include|:)?\s+([0-9,\sand]+)",
            content,
            re.IGNORECASE,
        ):
            for order_id in re.findall(r"\d{1,}", match.group(1)):
                add(int(order_id))

        for match in re.finditer(
            r"\border(?:s)?\s+((?:\d{1,}(?:\s*,\s*|\s+and\s+)?)+)",
            content,
            re.IGNORECASE,
        ):
            for order_id in re.findall(r"\d{1,}", match.group(1)):
                add(int(order_id))

    if not messages:
        return []

    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        scan(content)
    return order_ids


def _extract_order_ids_from_text(content: str) -> list[int]:
    return _extract_order_ids_from_history([{"role": "user", "content": content}])


def _looks_like_full_order_contents_request(question: str) -> bool:
    normalized = question.lower()
    return any(
        phrase in normalized
        for phrase in [
            "full content",
            "full contents",
            "every item",
            "all items",
            "everything on",
            "full list of items",
            "what was on order",
            "what is on order",
            "what's on order",
            "items on order",
            "order contents",
        ]
    )


def _extract_placed_by_customer_request(question: str) -> str | None:
    patterns = [
        r"\bwhat orders has\s+(.+?)\s+placed\b",
        r"\bwhat orders did\s+(.+?)\s+place\b",
        r"\blist orders\s+(.+?)\s+placed\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, question, re.IGNORECASE)
        if match:
            customer = re.sub(r"\s+and\s+what.*$", "", match.group(1), flags=re.IGNORECASE)
            return customer.strip(" ?.,")
    return None


def _extract_order_identity_request(question: str) -> int | None:
    normalized = question.lower()
    if not any(phrase in normalized for phrase in ["who placed", "who ordered", "who put in"]):
        return None
    match = re.search(r"\border\s+#?(\d{1,})\b", question, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _extract_fulfillment_timing_request(question: str) -> int | None:
    normalized = question.lower()
    if not re.search(r"\bfu?l?fill", normalized):
        return None
    if not any(phrase in normalized for phrase in ["how long", "take", "took", "duration"]):
        return None
    match = re.search(r"\border\s+#?(\d{1,})\b", question, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _extract_oldest_unfulfilled_request(question: str) -> dict[str, Any] | None:
    normalized = question.lower()
    if not re.search(r"\bfu?l?fill", normalized):
        return None
    if not any(phrase in normalized for phrase in ["longest", "oldest", "taking the longest", "taken the longest"]):
        return None

    days = 180
    month_match = re.search(r"\blast\s+(\d+)\s+months?\b", normalized)
    if month_match:
        days = _months_to_days(int(month_match.group(1)))
    day_match = re.search(r"\blast\s+(\d+)\s+days?\b", normalized)
    if day_match:
        days = int(day_match.group(1))

    return {"days": days, "limit": 10, "max_orders": 50000}


def _extract_fulfillment_aging_report_request(question: str) -> dict[str, Any] | None:
    normalized = question.lower()
    if not re.search(r"\bfu?l?fill", normalized):
        return None
    if not any(phrase in normalized for phrase in ["longest", "taking the longest", "taken the longest"]):
        return None

    days = 180
    month_match = re.search(r"\blast\s+(\d+)\s+months?\b", normalized)
    if month_match:
        days = _months_to_days(int(month_match.group(1)))
    day_match = re.search(r"\blast\s+(\d+)\s+days?\b", normalized)
    if day_match:
        days = int(day_match.group(1))

    return {"days": days, "limit": 10, "max_orders": 50000}


def _looks_like_model_info_request(question: str) -> bool:
    normalized = question.lower()
    return any(
        phrase in normalized
        for phrase in [
            "what model are you",
            "which model are you",
            "what gpt model",
            "which gpt model",
            "what llm",
            "which llm",
        ]
    )


def _looks_like_secret_request(question: str) -> bool:
    normalized = question.lower()
    secret_terms = [
        "api key",
        "apikey",
        "access token",
        "bc_access_token",
        "llm_api_key",
        "token",
        "secret",
        "password",
        ".env",
        "env file",
        "environment variable",
        "environment variables",
    ]
    allowed_model_terms = [
        "model",
        "llm_model",
        "what gpt",
        "which gpt",
    ]
    if any(term in normalized for term in allowed_model_terms) and "key" not in normalized and "token" not in normalized:
        return False
    return any(term in normalized for term in secret_terms)


def _same_month_day_prior_year(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return date(value.year - 1, value.month, 28)


def _ytd_comparison_periods(reference_day: date | None = None) -> dict[str, Any]:
    today = reference_day or date.today()
    current_start = date(today.year, 1, 1)
    prior_start = date(today.year - 1, 1, 1)
    prior_cutoff = _same_month_day_prior_year(today)
    prior_end_exclusive = prior_cutoff + timedelta(days=1)
    return {
        "current_year": today.year,
        "prior_year": today.year - 1,
        "current_start": current_start.isoformat(),
        "current_end": None,
        "prior_start": prior_start.isoformat(),
        "prior_end": prior_end_exclusive.isoformat(),
        "period_label": (
            f"{current_start.isoformat()} through {today.isoformat()} "
            f"vs {prior_start.isoformat()} through {prior_cutoff.isoformat()}"
        ),
    }


def _comparison_metric_focus(question: str) -> str | None:
    normalized = question.lower()
    if any(term in normalized for term in ["dollar", "revenue", "sales", "money"]):
        return "dollars"
    if any(term in normalized for term in ["quantity", "items", "units", "volume"]):
        return "quantity"
    if any(term in normalized for term in ["order count", "orders", "order volume"]):
        return "orders"
    return None


def _extract_ytd_comparison_request(
    question: str,
    _history: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    normalized = question.lower()
    comparison_terms = [
        "compared to last year",
        "compare to last year",
        "compared with last year",
        "compare with last year",
        "vs last year",
        "versus last year",
        "against last year",
        "year over year",
        "yoy",
        "same period last year",
        "same time last year",
        "at this time last year",
        "last year at this time",
    ]
    has_comparison = any(term in normalized for term in comparison_terms)
    has_progress_check = any(
        phrase in normalized
        for phrase in [
            "how are we doing",
            "how are we performing",
            "how is it going",
            "where are we",
            "doing so far",
        ]
    )
    has_year_context = (
        str(date.today().year) in normalized
        or "this year" in normalized
        or "year to date" in normalized
        or "ytd" in normalized
        or "so far" in normalized
    )

    if not has_comparison and not (has_progress_check and has_year_context):
        return None
    if any(term in normalized for term in ["breakdown", " by college", " by unit", "per college"]):
        return None

    periods = _ytd_comparison_periods()
    metric_focus = _comparison_metric_focus(question)
    return {
        **periods,
        "metric_focus": metric_focus,
    }


def _last_assistant_message(history: list[dict[str, Any]] | None) -> str:
    if not history:
        return ""
    for message in reversed(history):
        if message.get("role") == "assistant" and isinstance(message.get("content"), str):
            return message["content"]
    return ""


def _looks_like_ytd_comparison_answer(content: str) -> bool:
    lowered = content.lower()
    return (
        "year-to-date comparison" in lowered
        or "compared to the same period last year" in lowered
        or (
            "quantity sold" in lowered
            and len(re.findall(r"\b20\d{2}\b", content)) >= 2
        )
    )


def _extract_comparison_followup_request(
    question: str,
    history: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    normalized = question.lower().strip()
    if len(normalized) > 120:
        return None

    metric_focus = _comparison_metric_focus(question)
    if metric_focus is None:
        return None

    if _extract_ytd_comparison_request(question, history):
        return None

    if not _looks_like_ytd_comparison_answer(_last_assistant_message(history)):
        return None

    periods = _ytd_comparison_periods()
    return {
        **periods,
        "metric_focus": metric_focus,
    }


def _format_pct_change(current_value: float, prior_value: float) -> str:
    if prior_value == 0:
        return "unchanged" if current_value == 0 else "+100.0%"
    delta = current_value - prior_value
    pct = (delta / prior_value) * 100
    return f"{pct:+.1f}%"


def _format_delta_phrase(current_value: float, prior_value: float) -> str:
    delta = current_value - prior_value
    if delta == 0:
        return "unchanged"
    direction = "up" if delta > 0 else "down"
    amount = (
        _format_number(int(abs(delta)))
        if float(abs(delta)).is_integer()
        else f"{abs(delta):,.1f}"
    )
    return f"{direction} {amount} ({_format_pct_change(current_value, prior_value)})"


def _format_ytd_comparison_answer(request: dict[str, Any], current: dict[str, Any], prior: dict[str, Any]) -> str:
    metric_focus = request.get("metric_focus")
    current_year = request["current_year"]
    prior_year = request["prior_year"]
    current_sales = float(current.get("total_sales_inc_tax") or 0)
    prior_sales = float(prior.get("total_sales_inc_tax") or 0)
    current_orders = int(current.get("order_count") or 0)
    prior_orders = int(prior.get("order_count") or 0)
    current_quantity = int(current.get("item_quantity") or 0)
    prior_quantity = int(prior.get("item_quantity") or 0)

    if metric_focus == "dollars":
        return "\n".join(
            [
                f"Year-to-date sales comparison ({request['period_label']}):",
                f"- {prior_year}: {_format_money(prior_sales)}",
                f"- {current_year}: {_format_money(current_sales)}",
                f"- Change: {_format_delta_phrase(current_sales, prior_sales)}",
            ]
        )

    if metric_focus == "quantity":
        return "\n".join(
            [
                f"Year-to-date quantity comparison ({request['period_label']}):",
                f"- {prior_year}: {_format_number(prior_quantity)} items sold",
                f"- {current_year}: {_format_number(current_quantity)} items sold",
                f"- Change: {_format_delta_phrase(float(current_quantity), float(prior_quantity))}",
            ]
        )

    if metric_focus == "orders":
        return "\n".join(
            [
                f"Year-to-date order comparison ({request['period_label']}):",
                f"- {prior_year}: {_format_number(prior_orders)} orders",
                f"- {current_year}: {_format_number(current_orders)} orders",
                f"- Change: {_format_delta_phrase(float(current_orders), float(prior_orders))}",
            ]
        )

    return "\n".join(
        [
            f"Year-to-date comparison ({request['period_label']}):",
            "",
            "| Year | Sales | Orders | Quantity sold |",
            "| --- | ---: | ---: | ---: |",
            (
                f"| {prior_year} | {_format_money(prior_sales)} | "
                f"{_format_number(prior_orders)} | {_format_number(prior_quantity)} |"
            ),
            (
                f"| {current_year} | {_format_money(current_sales)} | "
                f"{_format_number(current_orders)} | {_format_number(current_quantity)} |"
            ),
            "",
            "Change vs the same period last year:",
            f"- Sales: {_format_delta_phrase(current_sales, prior_sales)}",
            f"- Orders: {_format_delta_phrase(float(current_orders), float(prior_orders))}",
            f"- Quantity sold: {_format_delta_phrase(float(current_quantity), float(prior_quantity))}",
        ]
    )


def _financial_summary_for_period(
    start_date: str,
    end_date: str | None,
) -> dict[str, Any]:
    return call_tool(
        "get_order_financial_summary",
        {
            "start_date": start_date,
            "end_date": end_date,
            "exclude_statuses": ["Cancelled", "Declined", "Refunded"],
        },
    )


def _answer_from_ytd_comparison_request(request: dict[str, Any]) -> str:
    current = _financial_summary_for_period(request["current_start"], request["current_end"])
    prior = _financial_summary_for_period(request["prior_start"], request["prior_end"])
    if current.get("error"):
        return f"I could not load the current-year summary: {current['error']}"
    if prior.get("error"):
        return f"I could not load the prior-year summary: {prior['error']}"
    return _format_ytd_comparison_answer(request, current, prior)


def _extract_sales_by_dimension_request(question: str) -> dict[str, Any] | None:
    normalized = question.lower()
    if not any(term in normalized for term in ["breakdown", " by ", "per "]):
        return None
    if not any(term in normalized for term in ["sales", "revenue", "purchases", "orders"]):
        return None

    dimension = None
    if any(term in normalized for term in ["college/unit", "college unit", "college", "unit", "school"]):
        dimension = "college_unit"
    elif "department code" in normalized or "department" in normalized:
        dimension = "department_code"
    elif "account number" in normalized:
        dimension = "account_number"
    elif "recipient" in normalized:
        dimension = "recipient"
    else:
        return None

    days = 90
    month_match = re.search(r"\blast\s+(\d+)\s+months?\b", normalized)
    if month_match:
        days = _months_to_days(int(month_match.group(1)))
    day_match = re.search(r"\blast\s+(\d+)\s+days?\b", normalized)
    if day_match:
        days = int(day_match.group(1))
    if "this year" in normalized or "year to date" in normalized or "ytd" in normalized:
        days = max(1, (date.today() - date(date.today().year, 1, 1)).days + 1)

    return {
        "dimension": dimension,
        "days": days,
        "limit": 25,
        "max_orders": 1000,
    }


def _answer_from_sales_total_request(request: dict[str, Any]) -> str:
    answer = None
    try:
        cache_result = run_bigcommerce_readonly_query(request["sql"], limit=1)
        cache_status = cache_result.get("cache_status") or {}
        rows = cache_result.get("rows") or []
        if int(cache_status.get("order_count") or 0) > 0 and rows:
            row = rows[0]
            answer = _format_sales_total_result(
                row.get("total_sales"),
                row.get("order_count"),
                row.get("item_count"),
                request,
                "local analytics cache",
            )
    except Exception:
        answer = None

    if answer is None:
        live_result = READ_ONLY_TOOLS["get_revenue_summary"](**request["live_args"])
        answer = _format_sales_total_result(
            live_result.get("total_revenue_inc_tax"),
            live_result.get("included_order_count"),
            None,
            request,
            "live BigCommerce API",
        )
        if live_result.get("is_truncated"):
            answer += (
                f" Warning: order scan hit max_orders={live_result.get('max_orders')}; "
                "total may be incomplete."
            )
    return answer


def _answer_from_shipping_total_request(request: dict[str, Any]) -> str:
    answer = None
    try:
        cache_result = run_bigcommerce_readonly_query(request["sql"], limit=1)
        cache_status = cache_result.get("cache_status") or {}
        rows = cache_result.get("rows") or []
        if int(cache_status.get("order_count") or 0) > 0 and rows:
            row = rows[0]
            answer = _format_shipping_total_result(
                row.get("total_shipping_charged"),
                row.get("order_count"),
                request,
                "local analytics cache",
            )
    except Exception:
        answer = None

    if answer is None:
        live_result = READ_ONLY_TOOLS["get_shipping_charge_total"](**request["live_args"])
        answer = _format_shipping_total_result(
            live_result.get("matched_shipping_total_inc_tax"),
            live_result.get("matched_order_count"),
            request,
            "live BigCommerce API",
        )
        if live_result.get("is_truncated"):
            answer += (
                f" Warning: order scan hit max_orders={live_result.get('max_orders')}; "
                "total may be incomplete."
            )
    return answer


def _answer_from_revenue_summary_request(request: dict[str, Any]) -> str:
    result = call_tool(
        "get_order_financial_summary",
        {
            "start_date": request.get("start_date"),
            "end_date": request.get("end_date"),
            "exclude_statuses": request.get("exclude_statuses"),
        },
    )
    if not result.get("error"):
        return _format_order_financial_summary(result)

    live_result = READ_ONLY_TOOLS["get_revenue_summary"](
        start_date=request.get("start_date"),
        end_date=request.get("end_date"),
        max_orders=request.get("max_orders", 50000),
        exclude_statuses=request.get("exclude_statuses"),
    )
    answer = _format_sales_total_result(
        live_result.get("total_revenue_inc_tax"),
        live_result.get("included_order_count"),
        None,
        {
            "label": (
                f"calendar year {request.get('start_date', '')[:4]}"
                if request.get("start_date")
                else "the requested period"
            ),
        },
        "live BigCommerce API",
    )
    if live_result.get("is_truncated"):
        answer += (
            f" Warning: order scan hit max_orders={live_result.get('max_orders')}; "
            "total may be incomplete."
        )
    return answer


def _answer_from_shipping_spend_request(request: dict[str, Any]) -> str:
    if request.get("unsupported_actual_carrier_spend"):
        return (
            "The current BigCommerce order data does not expose actual carrier invoice "
            "cost or what the store paid carriers. I can report what customers were "
            "charged for shipping by method instead."
        )

    tool_args = {
        key: value
        for key, value in request.items()
        if key != "unsupported_actual_carrier_spend"
    }
    result = READ_ONLY_TOOLS["get_shipping_spend_by_method"](**tool_args)
    formatted = _format_shipping_spend(result)
    return formatted or "I could not calculate shipping charges for that request."


def _answer_from_ranked_order_request(request: dict[str, Any]) -> str:
    result = READ_ONLY_TOOLS["get_ranked_orders"](**request)
    return _add_order_links(_format_ranked_orders(result))


def _try_deterministic_analytics_answer(
    question: str,
    history: list[dict[str, Any]] | None = None,
) -> str | None:
    ytd_comparison_request = _extract_ytd_comparison_request(question, history)
    if ytd_comparison_request:
        return _answer_from_ytd_comparison_request(ytd_comparison_request)

    comparison_followup_request = _extract_comparison_followup_request(question, history)
    if comparison_followup_request:
        return _answer_from_ytd_comparison_request(comparison_followup_request)

    sales_total_request = _extract_sales_total_request(question)
    if sales_total_request:
        return _answer_from_sales_total_request(sales_total_request)

    revenue_summary_request = _extract_revenue_summary_request(question)
    if revenue_summary_request:
        return _answer_from_revenue_summary_request(revenue_summary_request)

    shipping_total_request = _extract_shipping_total_request(question)
    if shipping_total_request:
        return _answer_from_shipping_total_request(shipping_total_request)

    shipping_spend_request = _extract_shipping_spend_request(question)
    if shipping_spend_request:
        return _answer_from_shipping_spend_request(shipping_spend_request)

    ranked_order_args = _extract_ranked_order_request(question)
    if ranked_order_args:
        return _answer_from_ranked_order_request(ranked_order_args)

    dimension_request = _extract_sales_by_dimension_request(question)
    if dimension_request:
        result = READ_ONLY_TOOLS["get_sales_by_dimension"](**dimension_request)
        formatted = _format_purchase_breakdown(result)
        return formatted or "I could not build that sales breakdown."

    return None


def _extract_revenue_summary_request(question: str) -> dict[str, Any] | None:
    normalized = question.lower()
    wants_total = any(phrase in normalized for phrase in ["total revenue", "store revenue", "gross revenue"])
    wants_breakdown = any(phrase in normalized for phrase in ["breakdown", " by ", "per college", "per unit", "by college", "by unit"])
    if not wants_total or wants_breakdown:
        return None

    year_match = re.search(r"\b(20\d{2})\b", question)
    if year_match:
        year = int(year_match.group(1))
        start_date = f"{year}-01-01"
        current_year = date.today().year
        end_date = None if year == current_year else f"{year + 1}-01-01"
        return {
            "start_date": start_date,
            "end_date": end_date,
            "max_orders": 50000,
            "exclude_statuses": ["Cancelled", "Declined", "Refunded"],
        }

    if "this year" in normalized or "year to date" in normalized or "ytd" in normalized:
        year = date.today().year
        return {
            "start_date": f"{year}-01-01",
            "end_date": None,
            "max_orders": 50000,
            "exclude_statuses": ["Cancelled", "Declined", "Refunded"],
        }

    return None


def _extract_sales_total_request(question: str) -> dict[str, Any] | None:
    normalized = question.lower()
    if not any(term in normalized for term in ["sales", "revenue", "total sold", "total order"]):
        return None
    if any(term in normalized for term in ["breakdown", " by ", "per college", "per unit", "by college", "by unit"]):
        return None

    start_date: str | None = None
    end_date: str | None = None
    label: str | None = None

    year_match = re.search(r"\b(20\d{2})\b", question)
    if year_match:
        year = int(year_match.group(1))
        start_date = f"{year}-01-01"
        end_date = None if year == date.today().year else f"{year + 1}-01-01"
        label = f"calendar year {year}"
    elif "current calendar year" in normalized or "this calendar year" in normalized:
        year = date.today().year
        start_date = f"{year}-01-01"
        end_date = None
        label = "the current calendar year so far"
    elif "this year" in normalized or "year to date" in normalized or "ytd" in normalized:
        year = date.today().year
        start_date = f"{year}-01-01"
        end_date = None
        label = "the current calendar year so far"
    else:
        return None

    where_clauses = [
        f"date_created >= '{start_date}'",
        "status NOT IN ('Cancelled', 'Declined', 'Refunded')",
    ]
    if end_date:
        where_clauses.append(f"date_created < '{end_date}'")

    sql = (
        "SELECT COALESCE(SUM(total_inc_tax), 0) AS total_sales, "
        "COUNT(*) AS order_count, "
        "COALESCE(SUM(items_total), 0) AS item_count "
        "FROM bc_orders WHERE "
        + " AND ".join(where_clauses)
    )

    return {
        "sql": sql,
        "start_date": start_date,
        "end_date": end_date,
        "label": label,
        "live_args": {
            "start_date": start_date,
            "end_date": end_date,
            "max_orders": 5000,
            "exclude_statuses": ["Cancelled", "Declined", "Refunded"],
        },
    }


def _format_sales_total_result(
    total_sales: Any,
    order_count: Any,
    item_count: Any,
    request: dict[str, Any],
    source: str,
) -> str:
    item_part = (
        f" and {_format_number(int(item_count or 0))} items"
        if item_count is not None
        else ""
    )
    return (
        f"Total sales for {request.get('label')}: "
        f"{_format_money(float(total_sales or 0))} across "
        f"{_format_number(int(order_count or 0))} orders{item_part}. "
        "Cancelled, Declined, and Refunded orders are excluded. "
        f"Source: {source}."
    )


def _extract_ranked_order_request(question: str) -> dict[str, Any] | None:
    normalized = question.lower()
    if "order" not in normalized:
        return None

    sort_by = "total_inc_tax"
    direction = "desc"
    if any(word in normalized for word in ["largest", "biggest", "highest", "highest-value", "expensive"]):
        sort_by = "total_inc_tax"
        direction = "desc"
    elif any(word in normalized for word in ["smallest", "lowest", "lowest-value", "cheapest"]):
        sort_by = "total_inc_tax"
        direction = "asc"
    elif any(word in normalized for word in ["first", "earliest", "oldest"]):
        sort_by = "date_created"
        direction = "asc"
    elif any(phrase in normalized for phrase in ["latest", "newest", "most recent"]):
        sort_by = "date_created"
        direction = "desc"
    elif any(phrase in normalized for phrase in ["most items", "largest item", "most units"]):
        sort_by = "items_total"
        direction = "desc"
    else:
        return None

    args: dict[str, Any] = {
        "sort_by": sort_by,
        "direction": direction,
        "limit": 10,
        "max_orders": 5000,
        "exclude_statuses": ["Cancelled", "Declined", "Refunded"],
    }

    year_match = re.search(r"\b(20\d{2})\b", question)
    if year_match:
        year = int(year_match.group(1))
        args["start_date"] = f"{year}-01-01"
        args["end_date"] = None if year == date.today().year else f"{year + 1}-01-01"
        return args

    day_match = re.search(r"\blast\s+(\d+)\s+days?\b", normalized)
    if day_match:
        args["days"] = int(day_match.group(1))
        return args

    month_match = re.search(r"\blast\s+(\d+)\s+months?\b", normalized)
    if month_match:
        args["days"] = _months_to_days(int(month_match.group(1)))
        return args

    if "last week" in normalized:
        args["days"] = 7
        return args

    if "last month" in normalized:
        args["days"] = 31
        return args

    if "this year" in normalized or "year to date" in normalized or "ytd" in normalized:
        args["start_date"] = f"{date.today().year}-01-01"
        args["end_date"] = None
        return args

    if "all time" in normalized or "all-time" in normalized or "ever" in normalized:
        args["start_date"] = "2000-01-01"
        args["end_date"] = None
        return args

    return args


def _extract_shipping_spend_request(question: str) -> dict[str, Any] | None:
    normalized = question.lower()
    if "shipping" not in normalized and "fedex" not in normalized and "ups" not in normalized:
        return None
    if not any(
        word in normalized
        for word in ["spend", "spent", "cost", "costs", "paid", "pay", "charge", "charged", "charges", "revenue"]
    ):
        return None
    actual_spend_terms = [
        "we spent",
        "we have spent",
        "we've spent",
        "we paid",
        "we have paid",
        "we've paid",
        "our spend",
        "our shipping spend",
        "carrier invoice",
        "carrier cost",
        "actual cost",
        "actual spend",
    ]
    customer_charge_terms = [
        "customers charged",
        "charged customers",
        "customer paid",
        "customers paid",
        "charged for shipping",
        "shipping revenue",
        "shipping charges",
    ]
    if any(term in normalized for term in actual_spend_terms) and not any(
        term in normalized for term in customer_charge_terms
    ):
        return {"unsupported_actual_carrier_spend": True}

    method_keyword = None
    for candidate in ["FedEx", "UPS", "USPS", "DHL"]:
        if re.search(rf"\b{re.escape(candidate.lower())}\b", normalized):
            method_keyword = candidate
            break
    if not method_keyword:
        method_match = re.search(r"\bon\s+(.+?)\s+shipping\b", question, re.IGNORECASE)
        if method_match:
            method_keyword = method_match.group(1).strip(" ?.,")

    start_date = None
    end_date = None
    days = 90
    year_match = re.search(r"\b(20\d{2})\b", question)
    if year_match:
        year = int(year_match.group(1))
        start_date = f"{year}-01-01"
        end_date = None if year == date.today().year else f"{year + 1}-01-01"
    elif (
        "all time" in normalized
        or "all-time" in normalized
        or "all of time" in normalized
        or "for all of time" in normalized
        or "ever" in normalized
    ):
        start_date = "2000-01-01"
    elif "this year" in normalized or "year to date" in normalized or "ytd" in normalized:
        start_date = f"{date.today().year}-01-01"
    else:
        month_match = re.search(r"\blast\s+(\d+)\s+months?\b", normalized)
        if month_match:
            days = _months_to_days(int(month_match.group(1)))
        day_match = re.search(r"\blast\s+(\d+)\s+days?\b", normalized)
        if day_match:
            days = int(day_match.group(1))

    return {
        "method_keyword": method_keyword,
        "start_date": start_date,
        "end_date": end_date,
        "days": days,
        "max_orders": 50000,
        "max_shipping_address_orders": 5000,
        "exclude_statuses": ["Cancelled", "Declined", "Refunded"],
    }


def _extract_shipping_total_request(question: str) -> dict[str, Any] | None:
    normalized = question.lower()
    if "shipping" not in normalized:
        return None
    if not any(term in normalized for term in ["charged", "charge", "charges", "revenue"]):
        return None
    if not any(term in normalized for term in ["total", "all time", "all-time", "ever"]):
        return None

    exclude_order_ids = [
        int(match.group(1))
        for match in re.finditer(
            r"\bexclud(?:e|ing)\s+(?:order\s+#?)?(\d{1,})\b",
            question,
            re.IGNORECASE,
        )
    ]

    include_statuses: list[str] | None = None
    if re.search(r"\bcomplete(?:d)?\s+status\b|\bstatus\s+(?:is\s+)?complete(?:d)?\b", normalized):
        include_statuses = ["Completed", "Complete"]

    start_date = "2000-01-01"
    end_date = None
    year_match = re.search(r"\b(20\d{2})\b", question)
    if year_match:
        year = int(year_match.group(1))
        start_date = f"{year}-01-01"
        end_date = None if year == date.today().year else f"{year + 1}-01-01"

    where_clauses = ["1 = 1"]
    if start_date:
        where_clauses.append(f"date_created >= '{start_date}'")
    if end_date:
        where_clauses.append(f"date_created < '{end_date}'")
    if include_statuses:
        statuses = ", ".join(f"'{status}'" for status in include_statuses)
        where_clauses.append(f"status IN ({statuses})")
    else:
        where_clauses.append("status NOT IN ('Cancelled', 'Declined', 'Refunded')")
    if exclude_order_ids:
        ids = ", ".join(str(order_id) for order_id in sorted(set(exclude_order_ids)))
        where_clauses.append(f"id NOT IN ({ids})")

    sql = (
        "SELECT COALESCE(SUM(shipping_cost_inc_tax), 0) AS total_shipping_charged, "
        "COUNT(*) AS order_count "
        "FROM bc_orders WHERE "
        + " AND ".join(where_clauses)
    )

    return {
        "sql": sql,
        "live_args": {
            "start_date": start_date,
            "end_date": end_date,
            "days": 90,
            "max_orders": 5000,
            "include_statuses": include_statuses,
            "exclude_statuses": ["Cancelled", "Declined", "Refunded"],
            "exclude_order_ids": exclude_order_ids,
        },
        "include_statuses": include_statuses,
        "exclude_order_ids": exclude_order_ids,
        "start_date": start_date,
        "end_date": end_date,
    }


def _format_shipping_total_result(
    total_shipping_charged: Any,
    order_count: Any,
    request: dict[str, Any],
    source: str,
) -> str:
    filters: list[str] = []
    if request.get("include_statuses"):
        filters.append("status Completed/Complete only")
    if request.get("exclude_order_ids"):
        excluded = ", ".join(f"Order {order_id}" for order_id in request["exclude_order_ids"])
        filters.append(f"excluding {excluded}")
    range_label = (
        f"{request.get('start_date')} through {request.get('end_date') or 'now'}"
        if request.get("start_date") != "2000-01-01"
        else "all time"
    )
    filter_label = f" ({'; '.join(filters)})" if filters else ""
    return (
        f"Total customer shipping charges for {range_label}{filter_label}: "
        f"{_format_money(float(total_shipping_charged or 0))} across "
        f"{_format_number(int(order_count or 0))} orders. Source: {source}."
    )


def _months_to_days(months: int) -> int:
    return max(1, months * 31)


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _extract_cpu_family_sales_request(question: str) -> dict[str, Any] | None:
    normalized = question.lower()
    if not any(term in normalized for term in ["cpu", "processor", "apple silicon", "windows arm", "intel", "amd"]):
        return None
    if not any(term in normalized for term in ["sold", "sell", "sales", "purchased", "bought", "machines", "computers"]):
        return None
    if any(term in normalized for term in ["most popular", "best selling", "best-selling", "top product", "what product", "what computer", "what laptop"]):
        return None
    if not any(term in normalized for term in ["month", "months", "monthly", "over time", "trend", "breakdown", "compare", "comparison", "highest amount"]):
        return None

    family = None
    if re.search(r"\bamd\b", normalized):
        family = "AMD"
    elif "apple silicon" in normalized:
        family = "Apple silicon"
    elif "windows arm" in normalized or "snapdragon" in normalized or "qualcomm" in normalized:
        family = "Windows ARM"
    elif re.search(r"\bintel\b", normalized):
        family = "Intel"

    if family is None:
        return None

    today = date.today()
    start_date = date(today.year, 1, 1)
    end_date = _add_months(date(today.year, today.month, 1), 1)
    month_match = re.search(r"\blast\s+(\d+)\s+months?\b", normalized)
    if month_match:
        months = max(1, min(int(month_match.group(1)), 60))
        current_month = date(today.year, today.month, 1)
        start_date = _add_months(current_month, -(months - 1))
    elif "this year" in normalized or "year to date" in normalized or "ytd" in normalized:
        start_date = date(today.year, 1, 1)
    else:
        year_match = re.search(r"\b(20\d{2})\b", normalized)
        if year_match:
            year = int(year_match.group(1))
            start_date = date(year, 1, 1)
            end_date = date(year + 1, 1, 1)

    return {
        "family": family,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "group_by": "month",
        "rank_months": any(term in normalized for term in ["highest", "top", "most", "peak"]),
    }


def _extract_catalog_classified_product_sales_request(question: str) -> dict[str, Any] | None:
    normalized = question.lower()
    if not any(term in normalized for term in ["most popular", "best selling", "best-selling", "top", "sold the most"]):
        return None
    if not any(term in normalized for term in ["sold", "sell", "sales", "purchased", "bought"]):
        return None

    cpu_family = None
    if re.search(r"\bamd\b", normalized):
        cpu_family = "AMD"
    elif "apple silicon" in normalized:
        cpu_family = "Apple silicon"
    elif "windows arm" in normalized or "snapdragon" in normalized or "qualcomm" in normalized:
        cpu_family = "Windows ARM"
    elif re.search(r"\bintel\b", normalized):
        cpu_family = "Intel"
    if cpu_family is None:
        return None

    machine_form = "computer"
    if "laptop" in normalized or "notebook" in normalized:
        machine_form = "laptop"
    elif any(term in normalized for term in ["desktop", "tower", "aio", "all-in-one"]):
        machine_form = "desktop"

    today = date.today()
    start_date = date(today.year, 1, 1)
    end_date = _add_months(date(today.year, today.month, 1), 1)
    year_match = re.search(r"\b(20\d{2})\b", normalized)
    if year_match:
        year = int(year_match.group(1))
        start_date = date(year, 1, 1)
        end_date = date(year + 1, 1, 1) if year != today.year else end_date

    return {
        "cpu_family": cpu_family,
        "machine_form": machine_form,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "limit": 10,
    }


def _format_catalog_classified_product_sales(result: dict[str, Any], request: dict[str, Any]) -> str:
    products = result.get("products") or []
    label = " ".join(
        part
        for part in [request.get("cpu_family"), request.get("machine_form")]
        if part
    )
    range_label = f"{request.get('start_date')} through {request.get('end_date')}"
    if not products:
        return f"I did not find sold {label} products for {range_label}."

    top = products[0]
    lines = [
        (
            f"The most popular {label} sold for {range_label} was "
            f"{top.get('name') or 'Unknown product'} with {_format_number(top.get('quantity_sold') or 0)} units sold."
        ),
        "",
        "| Rank | Product | SKU | Units sold | Revenue | Orders |",
        "|---:|---|---|---:|---:|---:|",
    ]
    for index, product in enumerate(products[:10], start=1):
        lines.append(
            (
                f"| {index} | {product.get('name') or 'Unknown'} | "
                f"{product.get('sku') or ''} | "
                f"{_format_number(product.get('quantity_sold') or 0)} | "
                f"{_format_money(float(product.get('revenue_inc_tax') or 0))} | "
                f"{_format_number(product.get('order_count') or 0)} |"
            )
        )
    return "\n".join(lines)


def _format_cpu_family_month_ranking(result: dict[str, Any], request: dict[str, Any]) -> str:
    family = request["family"]
    if not result.get("is_reliable_for_cpu_family"):
        return (
            f"I can't reliably rank {family} CPU machine sales yet because the local catalog cache has "
            "0 products. CPU-family classification needs catalog/spec data; order-line text alone can "
            "miss most machines or misclassify graphics as CPUs. Run a catalog/cache sync first, then ask again."
        )

    periods = result.get("periods") or []
    ranked_periods = sorted(
        periods,
        key=lambda period: int(((period.get("cpu_families") or {}).get(family) or {}).get("quantity") or 0),
        reverse=True,
    )
    ranked_periods = [
        period
        for period in ranked_periods
        if int(((period.get("cpu_families") or {}).get(family) or {}).get("quantity") or 0) > 0
    ]
    if not ranked_periods:
        return (
            f"I did not find {family} CPU machine sales from "
            f"{request['start_date']} through {request['end_date']}."
        )

    top_period = ranked_periods[0]
    top_values = (top_period.get("cpu_families") or {}).get(family) or {}
    lines = [
        (
            f"The peak month for {family} CPU machine sales was "
            f"{top_period.get('period')}, with {_format_number(top_values.get('quantity') or 0)} units sold."
        ),
        "",
        "| Rank | Month | Units sold | Revenue | Top contributing products |",
        "|---:|---|---:|---:|---|",
    ]
    for index, period in enumerate(ranked_periods[:8], start=1):
        values = (period.get("cpu_families") or {}).get(family) or {}
        top_products = values.get("top_products") or []
        product_label = "; ".join(
            f"{product.get('name') or 'Unknown'} ({_format_number(product.get('quantity') or 0)})"
            for product in top_products[:3]
        )
        lines.append(
            (
                f"| {index} | {period.get('period') or 'unknown'} | "
                f"{_format_number(values.get('quantity') or 0)} | "
                f"{_format_money(float(values.get('revenue_inc_tax') or 0))} | "
                f"{product_label or 'none'} |"
            )
        )

    if result.get("classification_source"):
        lines.append("")
        lines.append(f"Classification source: {result['classification_source']}.")
    return "\n".join(lines)


def _resolve_first_name_from_history(
    customer: str,
    messages: list[dict[str, Any]],
) -> str:
    if not re.fullmatch(r"[A-Za-z]+", customer):
        return customer

    first_name = re.escape(customer)
    pattern = re.compile(rf"\b({first_name}\s+[A-Z][A-Za-z'-]+)\b", re.IGNORECASE)
    for message in reversed(messages):
        content = message.get("content")
        if not isinstance(content, str):
            continue
        matches = pattern.findall(content)
        if matches:
            return matches[-1]
    return customer


def ask(question: str, messages: list[dict[str, Any]] | None = None) -> tuple[str, list[dict[str, Any]]]:
    history = messages or [{"role": "system", "content": SYSTEM_PROMPT}]

    if _looks_like_secret_request(question):
        answer = (
            "I can't show environment variables, API keys, access tokens, or secrets. "
            "I can report the configured model name only."
        )
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        return answer, history

    if _looks_like_model_info_request(question):
        model = os.getenv("LLM_MODEL", "").strip() or "not set"
        answer = f"This local chat is configured to use `{model}` via `LLM_MODEL`."
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        return answer, history

    classified_product_request = _extract_catalog_classified_product_sales_request(question)
    if classified_product_request:
        result = call_tool(
            "get_catalog_classified_product_sales",
            {
                "cpu_family": classified_product_request["cpu_family"],
                "machine_form": classified_product_request["machine_form"],
                "start_date": classified_product_request["start_date"],
                "end_date": classified_product_request["end_date"],
                "limit": classified_product_request["limit"],
            },
        )
        if result.get("error"):
            answer = f"I could not calculate the product ranking from the local cache: {result['error']}"
        else:
            answer = _format_catalog_classified_product_sales(result, classified_product_request)
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        return answer, history

    cpu_family_request = _extract_cpu_family_sales_request(question)
    if cpu_family_request:
        result = call_tool(
            "get_cpu_family_sales_breakdown",
            {
                "start_date": cpu_family_request["start_date"],
                "end_date": cpu_family_request["end_date"],
                "group_by": cpu_family_request["group_by"],
            },
        )
        if result.get("error"):
            answer = f"I could not calculate the CPU-family breakdown from the local cache: {result['error']}"
        elif cpu_family_request.get("rank_months"):
            answer = _format_cpu_family_month_ranking(result, cpu_family_request)
        else:
            answer = _format_cpu_family_sales_breakdown(result)
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        return answer, history

    deterministic_answer = _try_deterministic_analytics_answer(question, history)
    if deterministic_answer:
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": deterministic_answer})
        return deterministic_answer, history

    cache_answer = None
    cache_failed = False
    cache_loop_error: str | None = None
    try:
        cache_answer, cache_failed = _run_primary_cache_chat(question, history)
    except Exception as exc:
        cache_answer = None
        cache_failed = True
        cache_loop_error = str(exc)
    if cache_answer:
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": cache_answer})
        return cache_answer, history

    fulfillment_order_id = _extract_fulfillment_timing_request(question)
    if fulfillment_order_id:
        result = READ_ONLY_TOOLS["get_order_fulfillment_timing"](order_id=fulfillment_order_id)
        answer = _add_order_links(_format_fulfillment_timing(result))
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        return answer, history

    fulfillment_aging_args = _extract_fulfillment_aging_report_request(question)
    if fulfillment_aging_args and not any(word in question.lower() for word in ["currently", "still", "open", "unfulfilled"]):
        result = READ_ONLY_TOOLS["get_fulfillment_aging_report"](**fulfillment_aging_args)
        answer = _add_order_links(_format_fulfillment_aging_report(result))
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        return answer, history

    oldest_unfulfilled_args = _extract_oldest_unfulfilled_request(question)
    if oldest_unfulfilled_args:
        result = READ_ONLY_TOOLS["get_oldest_unfulfilled_orders"](**oldest_unfulfilled_args)
        answer = _add_order_links(_format_oldest_unfulfilled(result))
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        return answer, history

    placed_by_customer = _extract_placed_by_customer_request(question)
    if placed_by_customer:
        placed_by_customer = _resolve_first_name_from_history(placed_by_customer, history)
        result = READ_ONLY_TOOLS["get_full_order_contents_for_placed_by_customer"](
            customer=placed_by_customer,
            days=365,
            limit=50,
            max_orders=1000,
        )
        answer = _add_order_links(_format_order_contents(result))
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        return answer, history

    if _looks_like_full_order_contents_request(question):
        order_ids = _extract_order_ids_from_text(question) or _extract_order_ids_from_history(history)
        if order_ids:
            result = READ_ONLY_TOOLS["get_full_order_contents"](
                order_ids=order_ids,
                limit=min(len(order_ids), 100),
            )
            answer = _add_order_links(_format_order_contents(result))
            history.append({"role": "user", "content": question})
            history.append({"role": "assistant", "content": answer})
            return answer, history

    identity_order_id = _extract_order_identity_request(question)
    if identity_order_id:
        result = READ_ONLY_TOOLS["get_order_identity"](order_id=identity_order_id)
        answer = _add_order_links(_format_order_identity(result))
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        return answer, history

    if cache_loop_error:
        answer = (
            "Store Intelligence timed out while asking the language model to query the local cache. "
            "The cache may still be available, but I couldn't safely turn this question into a SQL answer this time."
        )
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        return answer, history

    history.append({"role": "user", "content": question})
    history_before_model = list(history)

    message = _coerce_text_tool_call(chat_completion(
        messages=history,
        tools=LIVE_TOOL_SCHEMAS,
        tool_choice="auto",
    ), allowed_tool_names=LIVE_TOOL_NAMES)
    history.append(_assistant_history_message(message))

    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        formatted_direct_answer, cache_failed = _run_tool_calls(tool_calls, history)

        if formatted_direct_answer:
            formatted_direct_answer = _add_order_links(formatted_direct_answer)
            history.append({"role": "assistant", "content": formatted_direct_answer})
            return formatted_direct_answer, history

        if cache_failed:
            fallback_history = [dict(item) for item in history_before_model]
            if fallback_history and fallback_history[0].get("role") == "system":
                fallback_history[0]["content"] = (
                    f"{SYSTEM_PROMPT}\n"
                    "The local BigCommerce analytics cache could not answer this request. "
                    "Use the live read-only BigCommerce API tools for this turn. Do not use cache SQL tools."
                )
            fallback_message = _coerce_text_tool_call(
                chat_completion(
                    messages=fallback_history,
                    tools=LIVE_TOOL_SCHEMAS,
                    tool_choice="auto",
                ),
                allowed_tool_names=LIVE_TOOL_NAMES,
            )
            history.append(_assistant_history_message(fallback_message))
            fallback_tool_calls = fallback_message.get("tool_calls") or []
            if fallback_tool_calls:
                fallback_answer, _ = _run_tool_calls(fallback_tool_calls, history)
                if fallback_answer:
                    fallback_answer = _add_order_links(fallback_answer)
                    history.append({"role": "assistant", "content": fallback_answer})
                    return fallback_answer, history
            fallback_answer = _prepare_final_answer(fallback_message.get("content") or "", question)
            if fallback_answer:
                history.append({"role": "assistant", "content": fallback_answer})
                return fallback_answer, history

        second = chat_completion(messages=history)
        answer = _prepare_final_answer(second.get("content") or "", question)
        history.append({"role": "assistant", "content": answer})
        return answer, history

    answer = _prepare_final_answer(message.get("content") or "", question)
    if not answer:
        answer = "I couldn't turn that into a clean answer. Please try the question again."
    if history and history[-1].get("role") == "assistant":
        history[-1]["content"] = answer
    else:
        history.append({"role": "assistant", "content": answer})
    return answer, history


def main() -> None:
    print("BigCommerce read-only chat. Type 'exit' to quit.")
    messages: list[dict[str, Any]] | None = None

    while True:
        question = input("\nAsk Store Intelligence: ").strip()
        if question.lower() in {"exit", "quit"}:
            break
        if not question:
            continue

        try:
            answer, messages = ask(question, messages)
        except Exception as exc:
            print(f"Error: {exc}")
            continue

        print(f"\n{answer}")


if __name__ == "__main__":
    main()
