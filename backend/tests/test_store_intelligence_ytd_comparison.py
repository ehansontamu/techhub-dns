from __future__ import annotations

import os
from datetime import date
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.services.bigcommerce_chat import chat_cli


def test_extract_ytd_comparison_request_matches_progress_question():
    request = chat_cli._extract_ytd_comparison_request(
        "How are we doing so far in 2026 compared to last year at this time?",
        None,
    )
    assert request is not None
    assert request["current_year"] == date.today().year
    assert request["prior_year"] == date.today().year - 1
    assert request["current_start"] == f"{date.today().year}-01-01"
    assert request["prior_start"] == f"{date.today().year - 1}-01-01"


def test_comparison_followup_detects_dollars_after_prior_answer():
    history = [
        {"role": "system", "content": "system"},
        {
            "role": "user",
            "content": "How are we doing so far in 2026 compared to last year at this time?",
        },
        {
            "role": "assistant",
            "content": "1. 2025 | quantity sold: 4,192\n2. 2026 | quantity sold: 4,383",
        },
    ]
    request = chat_cli._extract_comparison_followup_request(
        "How are we doing in terms of dollars?",
        history,
    )
    assert request is not None
    assert request["metric_focus"] == "dollars"


@patch("app.services.bigcommerce_chat.chat_cli.call_tool")
def test_answer_from_ytd_comparison_request_formats_both_periods(mock_call_tool):
    mock_call_tool.side_effect = [
        {
            "total_sales_inc_tax": 3309715,
            "order_count": 933,
            "item_quantity": 4383,
            "subtotal_inc_tax": 3308035,
            "shipping_inc_tax": 1680,
            "savings_available": False,
        },
        {
            "total_sales_inc_tax": 3000000,
            "order_count": 900,
            "item_quantity": 4192,
            "subtotal_inc_tax": 2990000,
            "shipping_inc_tax": 1500,
            "savings_available": False,
        },
    ]

    request = chat_cli._extract_ytd_comparison_request(
        "How are we doing so far in 2026 compared to last year at this time?",
        None,
    )
    answer = chat_cli._answer_from_ytd_comparison_request(request)

    assert "Year-to-date comparison" in answer
    assert str(date.today().year) in answer
    assert str(date.today().year - 1) in answer
    assert "$3,309,715" in answer
    assert "4,383" in answer
    assert "Change vs the same period last year" in answer
    assert mock_call_tool.call_count == 2


def test_year_period_sql_rows_skip_ranking_formatter():
    rows = [
        {"year": 2025, "quantity_sold": 4192},
        {"year": 2026, "quantity_sold": 4383},
    ]
    columns = ["year", "quantity_sold"]
    assert chat_cli._format_ranking_sql_rows(rows, columns) is None
    formatted = chat_cli._format_year_period_sql_rows(rows, columns)
    assert formatted is not None
    assert "Period comparison" in formatted
    assert "2025" in formatted
    assert "2026" in formatted