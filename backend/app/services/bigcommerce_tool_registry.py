from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.services.bigcommerce_analytics_cache import (
    get_bigcommerce_analytics_schema,
    get_bigcommerce_cache_status,
    get_catalog_classified_product_sales,
    get_catalog_filtered_product_sales,
    get_catalog_product_profile,
    get_cpu_family_sales_breakdown,
    get_order_financial_summary,
    run_bigcommerce_readonly_query,
    search_catalog_cache,
)
from app.services.bigcommerce_chat.bigcommerce_tools import READ_ONLY_TOOLS

ToolCallable = Callable[..., dict[str, Any]]

WAREHOUSE_TOOL_NAMES = {
    "get_bigcommerce_analytics_schema",
    "get_bigcommerce_cache_status",
    "get_catalog_classified_product_sales",
    "get_catalog_filtered_product_sales",
    "get_catalog_product_profile",
    "get_cpu_family_sales_breakdown",
    "get_order_financial_summary",
    "run_bigcommerce_readonly_query",
    "search_catalog_cache",
}

CHAT_TOOLS: dict[str, ToolCallable] = {
    **READ_ONLY_TOOLS,
    "get_bigcommerce_analytics_schema": get_bigcommerce_analytics_schema,
    "get_bigcommerce_cache_status": get_bigcommerce_cache_status,
    "get_catalog_classified_product_sales": get_catalog_classified_product_sales,
    "get_catalog_filtered_product_sales": get_catalog_filtered_product_sales,
    "get_catalog_product_profile": get_catalog_product_profile,
    "get_cpu_family_sales_breakdown": get_cpu_family_sales_breakdown,
    "get_order_financial_summary": get_order_financial_summary,
    "run_bigcommerce_readonly_query": run_bigcommerce_readonly_query,
    "search_catalog_cache": search_catalog_cache,
}


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if name not in CHAT_TOOLS:
        return {"error": f"Tool is not allowed: {name}"}

    try:
        return CHAT_TOOLS[name](**(arguments or {}))
    except Exception as exc:
        return {"error": str(exc)}
