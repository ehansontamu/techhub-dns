#!/usr/bin/env python3
"""Regression checks for order-details email workflow helpers."""

import os
import sys
from datetime import date
from typing import Any, cast

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

sys.path.append(".")

from app.database import Base
from app.models.order import Order, OrderStatus
from app.services.analytics_service import AnalyticsService
from app.services.order_service import OrderService


def _make_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return Session(), engine


def test_append_order_details_sent_marker_for_existing_remarks():
    existing = "Deliver to ACAD 101"
    updated = OrderService._append_order_details_sent_marker(existing)

    assert updated == "Deliver to ACAD 101\n\nOrder Details Sent"
    print("[PASS] append marker adds two-line separator")


def test_append_order_details_sent_marker_is_idempotent():
    existing = "Deliver to ACAD 101\n\nOrder Details Sent"
    updated = OrderService._append_order_details_sent_marker(existing)

    assert updated is None
    print("[PASS] append marker does not duplicate existing marker")


def test_append_order_details_sent_marker_for_empty_remarks():
    updated = OrderService._append_order_details_sent_marker("")

    assert updated == "Order Details Sent"
    print("[PASS] append marker handles empty remarks")


def test_is_business_day_filters_weekends():
    assert AnalyticsService._is_business_day(date(2026, 3, 16)) is True
    assert AnalyticsService._is_business_day(date(2026, 3, 21)) is False
    assert AnalyticsService._is_business_day("2026-03-22") is False
    print("[PASS] business-day helper filters weekends")


def test_workflow_daily_trends_skip_weekend_rows():
    service = AnalyticsService(db=cast(Any, None))
    start_date = date(2026, 3, 20)  # Friday
    current_date = start_date
    rows = []

    while current_date <= date(2026, 3, 23):
        if service._is_business_day(current_date):
            rows.append(current_date.isoformat())
        current_date = current_date.fromordinal(current_date.toordinal() + 1)

    assert rows == ["2026-03-20", "2026-03-23"]
    print("[PASS] workflow chart date range excludes weekend rows")


def test_archived_orders_still_count_in_analytics():
    session, engine = _make_session()
    try:
        order = Order(
            inflow_order_id="TH3001",
            status=OrderStatus.DELIVERED.value,
            archived_from_order_list=True,
        )
        session.add(order)
        session.commit()

        service = AnalyticsService(session)
        counts = service.get_order_status_counts()

        assert counts == {OrderStatus.DELIVERED.value: 1}
        print("[PASS] archived orders remain in analytics totals")
    finally:
        session.close()
        engine.dispose()


if __name__ == "__main__":
    print("Running order-details workflow regression tests...")
    print()

    test_append_order_details_sent_marker_for_existing_remarks()
    test_append_order_details_sent_marker_is_idempotent()
    test_append_order_details_sent_marker_for_empty_remarks()
    test_is_business_day_filters_weekends()
    test_workflow_daily_trends_skip_weekend_rows()
    test_archived_orders_still_count_in_analytics()

    print()
    print("[SUCCESS] Order-details workflow regression tests passed!")
