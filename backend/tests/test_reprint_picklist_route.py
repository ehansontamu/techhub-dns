#!/usr/bin/env python3
"""Route-level regression tests for picklist reprint lookup behavior."""

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


def _make_session():
    temp_dir = tempfile.TemporaryDirectory()
    db_path = Path(temp_dir.name) / "reprint_route.sqlite"
    engine = create_engine(f"sqlite:///{db_path}")
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    return temp_dir, engine, TestingSession()


def test_reprint_picklist_accepts_inflow_order_id(monkeypatch):
    temp_dir, engine, session = _make_session()
    try:
        order = Order(
            inflow_order_id="TH1004",
            status=OrderStatus.PICKED.value,
            picklist_path=str(Path(temp_dir.name) / "picklist.pdf"),
        )
        session.add(order)
        session.commit()
        session.refresh(order)

        monkeypatch.setattr(system_routes, "get_db_session", lambda: session)
        monkeypatch.setattr(
            system_routes, "get_current_user_email", lambda: "admin@example.com"
        )
        monkeypatch.setattr(system_routes, "emit_print_job_available", lambda _job: None)
        monkeypatch.setattr(system_routes, "emit_orders_update", lambda _message: None)

        app = Flask(__name__)
        with app.test_request_context(
            "/api/system/orders/TH1004/reprint-picklist",
            method="POST",
        ):
            response = system_routes.reprint_picklist.__wrapped__("TH1004")

        payload = response.get_json() or {}
        assert response.status_code == 200
        assert payload["success"] is True
        assert payload["job"]["order_id"] == order.id
        assert payload["job"]["order_inflow_order_id"] == "TH1004"
        assert payload["job"]["trigger_source"] == "manual"
    finally:
        session.close()
        engine.dispose()
        temp_dir.cleanup()


if __name__ == "__main__":
    test_reprint_picklist_accepts_inflow_order_id()
    print("[SUCCESS] reprint picklist route test passed")
