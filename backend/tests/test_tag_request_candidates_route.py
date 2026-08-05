#!/usr/bin/env python3
"""Regression tests for tag request candidate filtering."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.append(".")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.api.routes import orders as orders_routes
from app.database import Base
from app.models.order import Order, OrderStatus


def _make_session_factory():
    db_path = Path(__file__).with_name("tag_request_candidates.sqlite")
    if db_path.exists():
        db_path.unlink()

    engine = create_engine(f"sqlite:///{db_path}")
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    return db_path, engine, testing_session


def test_parent_remainder_without_taggable_items_is_excluded_from_candidates(monkeypatch):
    db_path, engine, session_factory = _make_session_factory()
    try:
        session = session_factory()
        order = Order(
            inflow_order_id="TH6001",
            status=OrderStatus.PICKED.value,
            has_remainder="Y",
            remainder_order_id="child-order-1",
            inflow_data={"lines": [{"productId": "prod-aio"}]},
        )
        session.add(order)
        session.commit()
        session.close()

        class _FakeInflowService:
            def requires_asset_tags_cached(self, _inflow_data, _cache):
                return True

        monkeypatch.setattr(orders_routes, "get_db", session_factory)
        monkeypatch.setattr(orders_routes, "InflowService", lambda: _FakeInflowService())

        app = Flask(__name__)
        with app.test_request_context("/api/orders/tag-request/candidates", method="GET"):
            with patch.object(
                orders_routes.OrderService,
                "_requires_asset_tags",
                return_value=False,
            ), patch.object(
                orders_routes.OrderService,
                "_parent_remainder_has_unpicked_items",
                return_value=False,
            ):
                response = orders_routes.get_tag_request_candidates.__wrapped__()

        payload = response.get_json() or []
        assert response.status_code == 200
        assert payload == []
    finally:
        engine.dispose()
        if db_path.exists():
            db_path.unlink()


def test_bulk_tag_marks_selected_orders_and_records_the_actor(monkeypatch):
    db_path, engine, session_factory = _make_session_factory()
    try:
        session = session_factory()
        order = Order(
            inflow_order_id="TH6002",
            status=OrderStatus.PICKED.value,
            inflow_data={"lines": [{"productId": "prod-aio"}]},
        )
        session.add(order)
        session.commit()
        order_id = str(order.id)
        session.close()

        monkeypatch.setattr(orders_routes, "get_db", session_factory)
        monkeypatch.setattr(
            orders_routes, "get_current_user_email", lambda: "tagger@example.com"
        )
        monkeypatch.setattr(
            orders_routes.broadcast_dedup,
            "request_broadcast",
            lambda _callback: None,
        )

        app = Flask(__name__)
        with app.test_request_context(
            "/api/orders/bulk-tag",
            method="POST",
            json={"order_ids": [order_id]},
        ):
            with patch.object(
                orders_routes.OrderService, "_requires_asset_tags", return_value=False
            ):
                response = orders_routes.bulk_tag_orders.__wrapped__()

        payload = response.get_json() or {}
        assert response.status_code == 200
        assert payload["success"] is True
        assert payload["updated_orders"] == [
            {"id": order_id, "inflow_order_id": "TH6002"}
        ]
        assert payload["failed_orders"] == []

        refreshed = session_factory().get(Order, order_id)
        assert refreshed.tagged_at is not None
        assert refreshed.tagged_by == "tagger@example.com"
    finally:
        engine.dispose()
        if db_path.exists():
            db_path.unlink()
