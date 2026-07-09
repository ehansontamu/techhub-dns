#!/usr/bin/env python3
"""Route-level regression tests for Canopy order uploads."""

import os
import sys
import tempfile
from pathlib import Path

from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.append(".")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.api.routes import system as system_routes
from app.database import Base
from app.models.order import Order, OrderStatus


def _make_session_factory():
    temp_dir = tempfile.TemporaryDirectory()
    db_path = Path(temp_dir.name) / "canopy_orders.sqlite"
    engine = create_engine(f"sqlite:///{db_path}")
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    return temp_dir, engine, testing_session


def test_partial_leg_canopy_upload_uses_parent_order_number(monkeypatch):
    temp_dir, engine, session_factory = _make_session_factory()
    try:
        session = session_factory()
        order = Order(
            inflow_order_id="TH4938-P2",
            status=OrderStatus.PICKED.value,
            inflow_data={"lines": [{"productId": "prod-aio"}]},
        )
        session.add(order)
        session.commit()
        session.close()

        class _FakeInflowService:
            def requires_asset_tags(self, _inflow_data):
                return True

        class _FakeUploader:
            def __init__(self):
                self.uploaded_orders = None
                self.notified_orders = None

            def upload_orders(self, orders):
                self.uploaded_orders = list(orders)
                return {
                    "success": True,
                    "filename": "canopyorders_test.json",
                    "uploaded_url": "https://example.com/canopyorders_test.json",
                }

            def send_teams_notification(self, orders, uploaded_url):
                self.notified_orders = list(orders)
                return bool(uploaded_url)

        fake_uploader = _FakeUploader()

        monkeypatch.setattr(system_routes, "get_db_session", session_factory)
        monkeypatch.setattr(
            system_routes, "CanopyOrdersUploaderService", lambda: fake_uploader
        )
        monkeypatch.setattr(
            system_routes, "InflowService", lambda: _FakeInflowService()
        )
        monkeypatch.setattr(
            system_routes, "get_current_user_email", lambda: "tech@example.com"
        )

        app = Flask(__name__)
        with app.test_request_context(
            "/api/system/canopyorders/upload",
            method="POST",
            json={"orders": ["TH4938-P2"]},
        ):
            response = system_routes.upload_canopy_orders.__wrapped__()

        payload = response.get_json() or {}
        refreshed_session = session_factory()
        try:
            refreshed = (
                refreshed_session.query(Order)
                .filter(Order.inflow_order_id == "TH4938-P2")
                .first()
            )
            assert refreshed is not None
            assert response.status_code == 200
            assert payload["success"] is True
            assert payload["eligible_orders"] == ["TH4938-P2"]
            assert payload["updated_orders"] == 1
            assert payload["teams_notified"] is True
            assert fake_uploader.uploaded_orders == ["TH4938"]
            assert fake_uploader.notified_orders == ["TH4938"]
            assert refreshed.tag_data is not None
            assert (
                refreshed.tag_data["canopyorders_request_filename"]
                == "canopyorders_test.json"
            )
            assert (
                refreshed.tag_data["canopyorders_request_uploaded_url"]
                == "https://example.com/canopyorders_test.json"
            )
            assert (
                refreshed.tag_data["canopyorders_request_sent_by"]
                == "tech@example.com"
            )
        finally:
            refreshed_session.close()
    finally:
        engine.dispose()
        temp_dir.cleanup()


def test_remainder_order_without_taggable_items_is_ineligible(monkeypatch):
    temp_dir, engine, session_factory = _make_session_factory()
    try:
        session = session_factory()
        order = Order(
            inflow_order_id="TH6002",
            status=OrderStatus.PICKED.value,
            has_remainder="Y",
            remainder_order_id="child-order-2",
            inflow_data={"lines": [{"productId": "prod-aio"}]},
        )
        session.add(order)
        session.commit()
        session.close()

        class _FakeUploader:
            def upload_orders(self, orders):
                raise AssertionError(f"Upload should not run for ineligible orders: {orders}")

            def send_teams_notification(self, orders, uploaded_url):
                raise AssertionError(
                    f"Notification should not run for ineligible orders: {orders}, {uploaded_url}"
                )

        monkeypatch.setattr(system_routes, "get_db_session", session_factory)
        monkeypatch.setattr(
            system_routes, "CanopyOrdersUploaderService", lambda: _FakeUploader()
        )

        app = Flask(__name__)
        with app.test_request_context(
            "/api/system/canopyorders/upload",
            method="POST",
            json={"orders": ["TH6002"]},
        ):
            with patch.object(
                system_routes.OrderService,
                "_requires_asset_tags",
                return_value=False,
            ):
                response = system_routes.upload_canopy_orders.__wrapped__()

        payload = response.get_json() or {}
        assert response.status_code == 400
        assert payload["error"] == "One or more orders are not eligible for upload."
        assert payload["eligible_orders"] == []
        assert payload["missing_orders"] == []
        assert payload["ineligible_orders"] == [
            {"order": "TH6002", "reason": "not asset-tag required"}
        ]
    finally:
        engine.dispose()
        temp_dir.cleanup()
