from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.services.bigcommerce_chat import chat_cli


def test_formats_single_row_sql_aggregate():
    result = {
        "columns": ["total_sales", "order_count"],
        "rows": [{"total_sales": 125000.5, "order_count": 42}],
        "row_count": 1,
        "truncated": False,
    }
    answer = chat_cli._format_sql_query_result(result, "total sales")
    assert answer is not None
    assert "$125,000" in answer
    assert "42" in answer


def test_formats_ranking_sql_rows_with_order_id():
    result = {
        "columns": ["order_id", "total_inc_tax"],
        "rows": [
            {"order_id": 1001, "total_inc_tax": 5000},
            {"order_id": 1002, "total_inc_tax": 4200},
        ],
        "row_count": 2,
        "truncated": False,
        "limit": 100,
    }
    answer = chat_cli._format_sql_query_result(result, "largest orders")
    assert answer is not None
    assert "Order 1001" in answer
    assert "$5,000" in answer


def test_returns_none_for_unrecognized_sql_shape():
    result = {
        "columns": ["note", "details", "extra"],
        "rows": [
            {"note": "alpha", "details": "beta", "extra": "gamma"},
            {"note": "delta", "details": "epsilon", "extra": "zeta"},
        ],
        "row_count": 2,
        "truncated": False,
    }
    assert chat_cli._format_sql_query_result(result, "details") is None