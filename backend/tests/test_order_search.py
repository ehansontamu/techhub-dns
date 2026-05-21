#!/usr/bin/env python3
"""Regression tests for order list search behavior."""

import os
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

backend_path = Path(__file__).parent.parent
sys.path.append(str(backend_path))

from app.database import Base
from app.models.order import Order, OrderStatus
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


def _make_order(
    *,
    inflow_order_id: str,
    recipient_name: str | None = None,
    delivery_location: str | None = None,
    po_number: str | None = None,
    inflow_sales_order_id: str | None = None,
    assigned_deliverer: str | None = None,
):
    now = datetime(2026, 5, 20, 12, 0, 0)
    return Order(
        inflow_order_id=inflow_order_id,
        inflow_sales_order_id=inflow_sales_order_id,
        recipient_name=recipient_name,
        delivery_location=delivery_location,
        po_number=po_number,
        assigned_deliverer=assigned_deliverer,
        status=OrderStatus.PICKED.value,
        created_at=now,
        updated_at=now,
    )


def test_get_orders_search_matches_listed_order_fields():
    session, engine = _make_session()
    try:
        session.add_all(
            [
                _make_order(
                    inflow_order_id="TH1001",
                    recipient_name="Ada Lovelace",
                    delivery_location="Engineering Building",
                    po_number="PO-1001",
                    inflow_sales_order_id="SO-1001",
                    assigned_deliverer="driver@example.com",
                ),
                _make_order(
                    inflow_order_id="TH1002",
                    recipient_name="Grace Hopper",
                    delivery_location="Research Annex",
                    po_number="PO-1002",
                    inflow_sales_order_id="SO-1002",
                ),
            ]
        )
        session.commit()

        service = OrderService(session)

        recipient_results, recipient_total = service.get_orders(search="Lovelace")
        location_results, location_total = service.get_orders(search="Annex")
        po_results, po_total = service.get_orders(search="PO-1001")
        sales_results, sales_total = service.get_orders(search="SO-1002")

        assert recipient_total == 1
        assert [order.inflow_order_id for order in recipient_results] == ["TH1001"]

        assert location_total == 1
        assert [order.inflow_order_id for order in location_results] == ["TH1002"]

        assert po_total == 1
        assert [order.inflow_order_id for order in po_results] == ["TH1001"]

        assert sales_total == 1
        assert [order.inflow_order_id for order in sales_results] == ["TH1002"]
    finally:
        session.close()
        engine.dispose()
