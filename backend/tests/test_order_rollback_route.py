#!/usr/bin/env python3
"""Route-level regression tests for order rollback wiring."""

import os
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
sys.path.append(".")

import types

stub_socketio = types.ModuleType("flask_socketio")
stub_socketio.emit = lambda *args, **kwargs: None
sys.modules.setdefault("flask_socketio", stub_socketio)

from app.api.routes import orders as orders_routes
from app.models.order import OrderStatus


@contextmanager
def _fake_db_context():
    yield SimpleNamespace()


def test_rollback_route_calls_service_and_serializes_response(monkeypatch):
    captured = {}

    class FakeRollbackUpdate:
        def __init__(self, **data):
            captured["parsed_payload"] = data
            self.status = OrderStatus.QA
            self.reason = data.get("reason")
            self.expected_updated_at = data.get("expected_updated_at")

    class FakeService:
        def __init__(self, db):
            captured["db"] = db

        def rollback_status(self, **kwargs):
            captured["service_args"] = kwargs
            return SimpleNamespace(id="order-1")

    def fake_response_json(order, db=None):
        captured["serialized_order"] = order
        captured["serialized_db"] = db
        return {"id": order.id, "status": "qa"}

    fake_broadcast = MagicMock()

    monkeypatch.setattr(orders_routes, "get_db", _fake_db_context)
    monkeypatch.setattr(orders_routes, "OrderRollbackUpdate", FakeRollbackUpdate)
    monkeypatch.setattr(orders_routes, "OrderService", FakeService)
    monkeypatch.setattr(orders_routes, "_order_response_json", fake_response_json)
    monkeypatch.setattr(orders_routes.broadcast_dedup, "request_broadcast", fake_broadcast)
    monkeypatch.setattr(orders_routes, "get_current_user_display_name", lambda: "Display User")

    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context(
        "/orders/order-1/rollback?changed_by=ops@example.com",
        method="PATCH",
        json={"status": "qa", "reason": "reopen for audit", "expected_updated_at": "2026-04-29T20:00:00Z"},
    ):
        response = orders_routes.rollback_order_status.__wrapped__("order-1")

    assert captured["parsed_payload"]["status"] == "qa"
    assert captured["service_args"]["order_id"] == "order-1"
    assert captured["service_args"]["target_status"] == OrderStatus.QA
    assert captured["service_args"]["changed_by"] == "ops@example.com"
    assert captured["service_args"]["reason"] == "reopen for audit"
    assert captured["service_args"]["expected_updated_at"] == "2026-04-29T20:00:00Z"
    assert captured["serialized_order"].id == "order-1"
    assert fake_broadcast.call_count == 1
    assert response.get_json() == {"id": "order-1", "status": "qa"}


if __name__ == "__main__":
    test_rollback_route_calls_service_and_serializes_response()
    print("[SUCCESS] order rollback route test passed")
