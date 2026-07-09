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
