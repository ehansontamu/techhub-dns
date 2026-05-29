from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from app.services.bigcommerce_chat.bigcommerce_tools import READ_ONLY_TOOLS
from app.services.bigcommerce_chat.llm_client import chat_completion

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


SYSTEM_PROMPT = """You are a read-only assistant for a BigCommerce store.
Use tools for store facts. Do not guess order statuses, totals, inventory, or customer details.
Never claim you can change, cancel, refund, edit, fulfill, or update anything.
When you mention a specific order ID, include the BigCommerce order link using https://store-jsj7fos9p1.mybigcommerce.com/manage/orders/{order_id}.
When asked for total/store-wide revenue, use get_revenue_summary. Do not use college/unit breakdowns unless the user explicitly asks for a breakdown/by college/by unit.
When asked what customers were charged for shipping by carrier or method, use get_shipping_spend_by_method. Do not treat shipping carriers like product keywords. When asked what the store/team spent or paid to carriers for shipping, explain that the current BigCommerce order API data does not expose actual carrier invoice cost.
For flexible analytics, prefer get_order_summary for filtered totals, get_grouped_order_summary for "by month/status/customer/college/product" breakdowns, and get_source_orders_for_summary when the user asks to audit or show the orders behind a number.
When asked which orders took the longest to fulfill, use get_fulfillment_aging_report so the answer includes both longest completed fulfillment durations and oldest currently-open orders. Use get_oldest_unfulfilled_orders only when the user explicitly asks for currently open/unfulfilled orders.
When asked how long a specific order took to fulfill, use get_order_fulfillment_timing.
When a user gives a name like "Jim's order", first search recent orders by customer name.
When asked who placed or ordered a specific order ID, use get_order_identity and include placed-by, recipient, shipping, and billing/contact context.
When asked about a department or company, count recent orders using company/name/message fields.
When asked about popular/top products for a college/unit, department code, account number, or recipient, use get_top_products_for_dimension_value.
Use get_top_products_sold_to only for older fuzzy customer/company text searches when no checkout dimension is implied.
When asked which customer bought the most of a product brand/type, treat customer as a person/name and use get_top_customers_for_product_keyword with group_by="person".
When asked who/customer bought the most of a product brand/type within a college/unit, department code, account number, or recipient, use get_top_customers_for_product_keyword_in_dimension.
For "customer" or "who ordered/placed", use the BigCommerce attached customer account from order.customer_id, not billing/shipping contact. Billing contact and recipient are separate context fields.
When asked which college, unit, school, group, or department bought the most, use get_top_customers_for_product_keyword with group_by="college_unit".
If the word customer seems ambiguous between a person and a college/unit, answer with the person view and mention the related college/unit values. Ask a brief follow-up only if the user's next decision depends on that distinction.
Treat typos and broad phrases like "HP machines" as product keyword searches.
When asked a follow-up like "what did he order" or "which items were those", use get_product_keyword_order_lines_for_customer with the customer and product keyword from context.
When asked for the full contents of orders, every item on those orders, or all items purchased on each order, use get_full_order_contents_for_customer_product_in_dimension when the context includes a customer, product keyword, and checkout dimension. Use get_full_order_contents when the user gives explicit order IDs.
When asked what orders a person placed, use get_full_order_contents_for_placed_by_customer. If the person only appears as billing/shipping contact, say that clearly and do not call those placed-by orders.
If a tool reports a tie, say it is a tie and list the tied customers instead of naming only one winner.
When asked for a percentage breakdown or comparison of Dell vs HP computers, use compare_computer_brand_sales_since.
When asked for purchases, sales, revenue, or order breakdown by college/unit, use get_sales_by_dimension with dimension="college_unit". The college/unit is the custom address field named Recipient College/Unit; do not combine it with Department Code.
When asked specifically for department code breakdowns, use get_sales_by_dimension with dimension="department_code".
When asked for account number breakdowns, use get_sales_by_dimension with dimension="account_number".
When asked for recipient breakdowns, use get_sales_by_dimension with dimension="recipient".
When asked for orders, top products, or comparisons for a checkout dimension value such as a college/unit, department code, account number, or recipient, use the dimension tools. Valid dimensions are college_unit, department_code, account_number, and recipient.
For these breakdowns, clearly say how many groups are displayed, how many total groups exist, how many displayed orders are covered, and include an "All other groups" summary when remaining_totals is nonzero.
For "since the beginning of 2026", pass start_date="2026-01-01".
Non-precise names can be aliases. "Bush School" can mean Bush or Bush School of Government and Public Service.
"Arts and Sciences" can mean College of Arts and Sciences or Arts & Sciences.
Keep answers concise and include order IDs when relevant.
"""


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
                    "exclude_statuses": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
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
            "name": "search_products",
            "description": "Search products by keyword.",
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
            "description": "Get product details by SKU.",
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
            "description": "Find products with tracked inventory at or below a threshold.",
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
            "description": "Find visible products that have no images.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 250}},
            },
        },
    },
]


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
    if name not in READ_ONLY_TOOLS:
        return json.dumps({"error": f"Tool is not allowed: {name}"})

    arguments = _clamp_tool_arguments(json.loads(arguments_json or "{}"))
    result = READ_ONLY_TOOLS[name](**arguments)
    return json.dumps(result, default=str)


def _format_money(value: float | int) -> str:
    return f"${value:,.0f}"


def _format_number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}"
    if isinstance(value, (int, float)):
        return f"{value:,}"
    return str(value)


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

    for match in re.finditer(r"\bOrder\s+#?(\d{4,})\b", text, re.IGNORECASE):
        add(match.group(1))

    for match in re.finditer(
        r"\bOrder IDs?(?:\s+include|:)?\s+([0-9,\sand]+)",
        text,
        re.IGNORECASE,
    ):
        for order_id in re.findall(r"\d{4,}", match.group(1)):
            add(order_id)

    for match in re.finditer(
        r"\border(?:s)?\s+((?:\d{4,}(?:\s*,\s*|\s+and\s+)?)+)",
        text,
        re.IGNORECASE,
    ):
        for order_id in re.findall(r"\d{4,}", match.group(1)):
            add(order_id)

    return order_ids


def _add_order_links(answer: str) -> str:
    order_ids = [
        order_id
        for order_id in _extract_relevant_order_ids(answer)
        if _order_url(order_id) not in answer
    ]
    if not order_ids:
        return answer

    lines = ["", "Order links:"]
    for order_id in order_ids:
        lines.append(f"- Order {order_id}: {_order_url(order_id)}")
    return f"{answer.rstrip()}\n" + "\n".join(lines)


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


def _format_popular_product_summary(result: dict[str, Any]) -> str:
    groups = result.get("groups") or []
    if not groups:
        return f"I did not find matching products for {_date_range_label(result)}."

    top = groups[0]
    lines = [
        (
            f"The most popular matching product for {_date_range_label(result)} was "
            f"{top.get('group')} with {_format_number(top.get('item_quantity') or 0)} "
            f"{_plural(top.get('item_quantity') or 0, 'item')} sold."
        ),
        (
            f"Revenue: {_format_money(top.get('total_inc_tax') or 0)} across "
            f"{top.get('order_count')} {_plural(top.get('order_count') or 0, 'order')}."
        ),
    ]

    if len(groups) > 1:
        lines.extend(["", "Next highest:"])
        for index, group in enumerate(groups[1:5], start=2):
            lines.append(
                (
                    f"{index}. {group.get('group')}: "
                    f"{_format_number(group.get('item_quantity') or 0)} items, "
                    f"{_format_money(group.get('total_inc_tax') or 0)}"
                )
            )

    if result.get("line_item_scan_truncated"):
        lines.append(
            (
                f"Warning: line-item scan was capped at "
                f"{result.get('line_item_orders_scanned')} orders "
                f"(limit {result.get('line_item_scan_limit')}); this may miss older orders."
            )
        )
    if result.get("is_truncated"):
        lines.append(
            f"Warning: order scan hit max_orders={result.get('max_orders')}; totals may be incomplete."
        )

    return "\n".join(lines)


def _format_popular_product_with_top_order(
    grouped_result: dict[str, Any],
    source_result: dict[str, Any],
) -> str:
    groups = grouped_result.get("groups") or []
    if not groups:
        return f"I did not find matching computer products for {_date_range_label(grouped_result)}."

    top_product = groups[0]
    product_name = str(top_product.get("group") or "Unknown product")

    order_rows: list[dict[str, Any]] = []
    for order in source_result.get("orders") or []:
        matching_products = order.get("matching_products") or []
        quantity = sum(int(product.get("quantity") or 0) for product in matching_products)
        line_total = sum(float(product.get("total_inc_tax") or 0) for product in matching_products)
        if quantity <= 0:
            continue
        order_rows.append(
            {
                "order": order,
                "quantity": quantity,
                "line_total": line_total,
            }
        )

    lines = [
        (
            f"The computer sold most often for {_date_range_label(grouped_result)} was "
            f"{product_name}: {_format_number(top_product.get('item_quantity') or 0)} "
            f"{_plural(top_product.get('item_quantity') or 0, 'unit')} sold."
        ),
        (
            f"That product accounted for {_format_money(top_product.get('total_inc_tax') or 0)} "
            f"across {top_product.get('order_count')} "
            f"{_plural(top_product.get('order_count') or 0, 'order')}."
        ),
    ]

    if order_rows:
        order_rows.sort(
            key=lambda row: (
                -int(row["quantity"]),
                -float(row["line_total"]),
                -int(row["order"].get("order_id") or 0),
            )
        )
        top_row = order_rows[0]
        top_order = top_row["order"]
        lines.extend(
            [
                "",
                (
                    f"The order with the most of that computer was Order "
                    f"{top_order.get('order_id')} ({_order_url(top_order.get('order_id'))}) "
                    f"with {_format_number(top_row['quantity'])} "
                    f"{_plural(top_row['quantity'], 'unit')}."
                ),
                (
                    f"Placed by {top_order.get('placed_by') or 'Unknown'} | "
                    f"{top_order.get('college_unit') or 'Unknown college/unit'} | "
                    f"{top_order.get('status') or 'Unknown status'} | "
                    f"matching line total {_format_money(top_row['line_total'])}."
                ),
            ]
        )
    else:
        lines.append("")
        lines.append("I found the top computer, but could not identify a source order for it within the scan cap.")

    if grouped_result.get("line_item_scan_truncated") or source_result.get("line_item_scan_truncated"):
        lines.append(
            (
                "Warning: the line-item scan was capped, so this can miss older "
                "orders in the requested range."
            )
        )
    if grouped_result.get("is_truncated") or source_result.get("is_truncated"):
        lines.append("Warning: the order scan was capped, so totals may be incomplete.")

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
                f"- Order {order.get('order_id')} ({_order_url(order.get('order_id'))}) | "
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


def _format_order_extreme(result: dict[str, Any], direction: str) -> str:
    orders = result.get("orders") or []
    if not orders:
        return f"I did not find any matching orders for {_date_range_label(result)}."

    def order_total(order: dict[str, Any]) -> float:
        try:
            return float(order.get("total_inc_tax") or 0)
        except (TypeError, ValueError):
            return 0.0

    reverse = direction == "largest"
    ranked_orders = sorted(orders, key=order_total, reverse=reverse)
    top_order = ranked_orders[0]
    top_total = order_total(top_order)
    tied_orders = [
        order
        for order in ranked_orders
        if order.get("order_id") != top_order.get("order_id") and order_total(order) == top_total
    ]

    adjective = "biggest" if direction == "largest" else "smallest"
    lines = [
        (
            f"The {adjective} order for {_date_range_label(result)} was "
            f"Order {top_order.get('order_id')} ({_order_url(top_order.get('order_id'))}) "
            f"at {_format_money(top_total)}."
        ),
        (
            f"Placed by {top_order.get('placed_by') or 'Unknown'} | "
            f"{top_order.get('college_unit') or 'Unknown college/unit'} | "
            f"{top_order.get('status') or 'Unknown status'} | "
            f"{top_order.get('date_created') or 'Unknown date'}."
        ),
    ]

    if tied_orders:
        tied_order_ids = ", ".join(f"Order {order.get('order_id')}" for order in tied_orders)
        lines.append(f"Tie at {_format_money(top_total)} with: {tied_order_ids}.")

    if result.get("matching_order_count"):
        lines.append(
            (
                f"Compared {result.get('matching_order_count')} matching "
                f"{_plural(int(result.get('matching_order_count') or 0), 'order')}."
            )
        )

    if result.get("is_truncated"):
        lines.append(
            f"Warning: this hit max_orders={result.get('max_orders')}; the ranking may be incomplete."
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
                f"{index}. Order {order.get('order_id')} ({_order_url(order.get('order_id'))}) | "
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
                f"{index}. Order {order.get('order_id')} ({_order_url(order.get('order_id'))}) | "
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
                f"{index}. Order {order.get('order_id')} ({_order_url(order.get('order_id'))}) | "
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
                f"Order {order_id} ({_order_url(order_id)}) took {duration} "
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
                f"Order {order_id} ({_order_url(order_id)}) is not fulfilled yet. "
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
        f"Order {order_id} ({_order_url(order_id)}) has status {result.get('status')}, "
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
                    f"Order {order.get('id')} ({_order_url(order.get('id'))}) | "
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
                    f"{_order_url(order.get('order_id'))} | "
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
        f"Order {result.get('order_id')} identity: {_order_url(result.get('order_id'))}",
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


def _extract_order_ids_from_history(messages: list[dict[str, Any]] | None) -> list[int]:
    seen: set[int] = set()
    order_ids: list[int] = []

    def add(order_id: int) -> None:
        if order_id not in seen:
            seen.add(order_id)
            order_ids.append(order_id)

    def scan(content: str) -> None:
        for match in re.finditer(r"\bOrder\s+(\d{4,})\b", content, re.IGNORECASE):
            add(int(match.group(1)))

        for match in re.finditer(
            r"\bOrder IDs?(?:\s+include|:)?\s+([0-9,\sand]+)",
            content,
            re.IGNORECASE,
        ):
            for order_id in re.findall(r"\d{4,}", match.group(1)):
                add(int(order_id))

        for match in re.finditer(
            r"\border(?:s)?\s+((?:\d{4,}(?:\s*,\s*|\s+and\s+)?)+)",
            content,
            re.IGNORECASE,
        ):
            for order_id in re.findall(r"\d{4,}", match.group(1)):
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
    match = re.search(r"\border\s+#?(\d{4,})\b", question, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _extract_fulfillment_timing_request(question: str) -> int | None:
    normalized = question.lower()
    if not re.search(r"\bfu?l?fill", normalized):
        return None
    if not any(phrase in normalized for phrase in ["how long", "take", "took", "duration"]):
        return None
    match = re.search(r"\border\s+#?(\d{4,})\b", question, re.IGNORECASE)
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


def _months_to_days(months: int) -> int:
    return max(1, months * 31)


def _extract_basic_date_range(question: str) -> dict[str, Any]:
    normalized = question.lower()

    year_match = re.search(r"\b(20\d{2})\b", question)
    if year_match:
        year = int(year_match.group(1))
        return {
            "start_date": f"{year}-01-01",
            "end_date": None if year == date.today().year else f"{year + 1}-01-01",
        }

    if "this year" in normalized or "year to date" in normalized or "ytd" in normalized:
        return {"start_date": f"{date.today().year}-01-01", "end_date": None}

    month_match = re.search(r"\blast\s+(\d+)\s+months?\b", normalized)
    if month_match:
        return {"days": _months_to_days(int(month_match.group(1)))}

    day_match = re.search(r"\blast\s+(\d+)\s+days?\b", normalized)
    if day_match:
        return {"days": int(day_match.group(1))}

    if "last week" in normalized or "past week" in normalized:
        return {"days": 7}

    return {"days": 90}


def _extract_order_extreme_request(question: str) -> dict[str, Any] | None:
    normalized = question.lower()
    if "order" not in normalized:
        return None

    largest_terms = [
        "biggest",
        "largest",
        "highest value",
        "highest-value",
        "highest total",
        "most expensive",
        "top order",
    ]
    smallest_terms = [
        "smallest",
        "lowest value",
        "lowest-value",
        "lowest total",
        "least expensive",
    ]

    direction = None
    if any(term in normalized for term in largest_terms):
        direction = "largest"
    elif any(term in normalized for term in smallest_terms):
        direction = "smallest"

    if direction is None:
        return None

    args = _extract_basic_date_range(question)
    args.update(
        {
            "direction": direction,
            "limit": 250,
            "max_orders": 50000,
            "exclude_statuses": ["Cancelled", "Declined", "Refunded"],
        }
    )
    return args


def _extract_popular_brand_product_request(question: str) -> dict[str, Any] | None:
    normalized = question.lower()
    if not any(phrase in normalized for phrase in ["most popular", "top selling", "top-selling", "best selling", "best-selling"]):
        return None

    group = None
    if any(word in normalized for word in ["machine", "machines", "computer", "computers", "laptop", "laptops", "desktop", "desktops", "workstation", "workstations"]):
        group = "computers"
    if not group:
        return None

    brand = None
    for candidate in ["Dell", "HP", "Lenovo", "Apple", "Microsoft"]:
        if re.search(rf"\b{re.escape(candidate.lower())}\b", normalized):
            brand = candidate
            break

    args = _extract_basic_date_range(question)
    args.update({
        "group_by": "product_name",
        "product_group": group,
        "brand": brand,
        "limit": 10,
        "sort_by": "items",
        "max_orders": WEB_MAX_ORDER_SCAN,
        "max_line_item_orders": min(WEB_MAX_LINE_ITEM_ORDER_SCAN, 250),
    })
    return args


def _looks_like_popular_product_top_order_request(question: str) -> bool:
    normalized = question.lower()
    return (
        "order" in normalized
        and any(phrase in normalized for phrase in ["most of that", "which order had", "what order had"])
        and _extract_popular_brand_product_request(question) is not None
    )


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

    order_extreme_args = _extract_order_extreme_request(question)
    if order_extreme_args:
        direction = order_extreme_args.pop("direction")
        result = READ_ONLY_TOOLS["get_source_orders_for_summary"](**order_extreme_args)
        answer = _format_order_extreme(result, direction)
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        return answer, history

    shipping_spend_args = _extract_shipping_spend_request(question)
    if shipping_spend_args:
        if shipping_spend_args.pop("unsupported_actual_carrier_spend", False):
            answer = (
                "I can't answer actual carrier shipping spend from the current BigCommerce "
                "order API data. BigCommerce orders can show what customers were charged "
                "for shipping, but they do not expose what your team paid FedEx/UPS/etc. "
                "To answer that, we'd need a carrier invoice/export source or another "
                "read-only table that contains actual carrier charges."
            )
        else:
            result = READ_ONLY_TOOLS["get_shipping_spend_by_method"](**shipping_spend_args)
            answer = _format_shipping_spend(result)
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        return answer, history

    revenue_args = _extract_revenue_summary_request(question)
    if revenue_args:
        result = READ_ONLY_TOOLS["get_revenue_summary"](**revenue_args)
        answer = _format_revenue_summary(result)
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        return answer, history

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

    popular_brand_args = _extract_popular_brand_product_request(question)
    if popular_brand_args:
        result = READ_ONLY_TOOLS["get_grouped_order_summary"](**popular_brand_args)
        if _looks_like_popular_product_top_order_request(question):
            top_groups = result.get("groups") or []
            if top_groups:
                top_product_name = str(top_groups[0].get("group") or "")
                source_args = {
                    key: value
                    for key, value in popular_brand_args.items()
                    if key
                    in {
                        "start_date",
                        "end_date",
                        "days",
                        "max_orders",
                        "max_line_item_orders",
                    }
                }
                source_args.update(
                    {
                        "product_keyword": top_product_name,
                        "limit": 250,
                        "exclude_statuses": ["Cancelled", "Declined", "Refunded"],
                    }
                )
                source_result = READ_ONLY_TOOLS["get_source_orders_for_summary"](**source_args)
                answer = _format_popular_product_with_top_order(result, source_result)
            else:
                answer = _format_popular_product_summary(result)
        else:
            answer = _format_popular_product_summary(result)
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

    history.append({"role": "user", "content": question})

    message = chat_completion(
        messages=history,
        tools=TOOL_SCHEMAS,
        tool_choice="auto",
    )
    history.append(_assistant_history_message(message))

    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        formatted_direct_answer: str | None = None
        for tool_call in tool_calls:
            function = tool_call.get("function") or {}
            name = function.get("name", "")
            arguments = function.get("arguments", "{}")
            if os.getenv("DEBUG_TOOLS") == "1":
                print(f"[tool] {name}({arguments})")
            result = _call_tool(name, arguments)
            if name in {"get_purchase_breakdown_by_college_unit", "get_sales_by_dimension"}:
                formatted_direct_answer = _format_purchase_breakdown(json.loads(result))
            if name == "get_revenue_summary":
                formatted_direct_answer = _format_revenue_summary(json.loads(result))
            if name == "get_order_summary":
                formatted_direct_answer = _format_order_summary(json.loads(result))
            if name == "get_grouped_order_summary":
                formatted_direct_answer = _format_grouped_order_summary(json.loads(result))
            if name == "get_source_orders_for_summary":
                formatted_direct_answer = _format_source_orders(json.loads(result))
            if name == "get_shipping_spend_by_method":
                formatted_direct_answer = _format_shipping_spend(json.loads(result))
            if name == "get_oldest_unfulfilled_orders":
                formatted_direct_answer = _format_oldest_unfulfilled(json.loads(result))
            if name == "get_fulfillment_aging_report":
                formatted_direct_answer = _format_fulfillment_aging_report(json.loads(result))
            if name == "get_order_fulfillment_timing":
                formatted_direct_answer = _format_fulfillment_timing(json.loads(result))
            if name == "get_order_identity":
                formatted_direct_answer = _format_order_identity(json.loads(result))
            if name in {
                "get_full_order_contents",
                "get_full_order_contents_for_customer_product_in_dimension",
                "get_full_order_contents_for_placed_by_customer",
            }:
                formatted_direct_answer = _format_order_contents(json.loads(result))
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.get("id", name),
                    "name": name,
                    "content": result,
                }
            )

        if formatted_direct_answer:
            formatted_direct_answer = _add_order_links(formatted_direct_answer)
            history.append({"role": "assistant", "content": formatted_direct_answer})
            return formatted_direct_answer, history

        second = chat_completion(messages=history)
        answer = _add_order_links(second.get("content") or "")
        history.append({"role": "assistant", "content": answer})
        return answer, history

    answer = _add_order_links(message.get("content") or "")
    return answer, history


def main() -> None:
    print("BigCommerce read-only chat. Type 'exit' to quit.")
    messages: list[dict[str, Any]] | None = None

    while True:
        question = input("\nAsk BigCommerce: ").strip()
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
