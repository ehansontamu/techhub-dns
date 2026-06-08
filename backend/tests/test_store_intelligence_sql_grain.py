from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest

from app.services.bigcommerce_analytics_cache import (
    BigCommerceAnalyticsQueryError,
    _validate_readonly_sql,
)


def test_rejects_order_total_sum_with_line_item_join():
    sql = """
        SELECT SUM(o.total_inc_tax) AS total_sales
        FROM bc_orders o
        JOIN bc_order_items i ON i.order_id = o.id
        WHERE o.status NOT IN ('Cancelled', 'Declined', 'Refunded')
    """
    with pytest.raises(BigCommerceAnalyticsQueryError, match="duplicates each order"):
        _validate_readonly_sql(sql)


def test_allows_order_total_sum_without_line_item_join():
    sql = """
        SELECT SUM(total_inc_tax) AS total_sales, COUNT(*) AS order_count
        FROM bc_orders
        WHERE status NOT IN ('Cancelled', 'Declined', 'Refunded')
    """
    assert "bc_orders" in _validate_readonly_sql(sql)


def test_allows_line_item_quantity_sum_with_join():
    sql = """
        SELECT SUM(i.quantity) AS quantity_sold
        FROM bc_orders o
        JOIN bc_order_items i ON i.order_id = o.id
        WHERE o.status NOT IN ('Cancelled', 'Declined', 'Refunded')
    """
    assert "bc_order_items" in _validate_readonly_sql(sql)


def test_allows_order_total_sum_when_grouped_by_order_id():
    sql = """
        SELECT SUM(order_total) AS total_sales
        FROM (
            SELECT o.id, MAX(o.total_inc_tax) AS order_total
            FROM bc_orders o
            JOIN bc_order_items i ON i.order_id = o.id
            GROUP BY o.id
        ) AS per_order
    """
    validated = _validate_readonly_sql(sql)
    assert "per_order" in validated