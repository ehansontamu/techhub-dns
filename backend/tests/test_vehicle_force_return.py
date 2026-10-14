"""Admin recovery preserves checkout attribution, orders, and active runs."""

from contextlib import contextmanager
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from flask import Flask, g
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app import models  # noqa: F401
from app.api import auth_middleware
from app.api.middleware import register_error_handlers
from app.api.routes import vehicle_checkouts
from app.database import Base
from app.models.audit_log import SystemAuditLog
from app.models.delivery_run import DeliveryRun, DeliveryRunStatus
from app.models.order import Order, OrderStatus
from app.models.user import User
from app.services.vehicle_checkout_service import VehicleCheckoutService


@pytest.fixture
def recovery(monkeypatch):
    # Independent engine: never reset or write to the configured database.
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    student = User(id=str(uuid4()), tamu_oid="test-student", email="student@example.com", display_name="Student One")
    admin = User(id=str(uuid4()), tamu_oid="test-admin", email="admin@example.com", display_name="Admin One")
    db.add_all([student, admin])
    db.commit()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(vehicle_checkouts.vehicle_checkouts_bp)
    register_error_handlers(app)
    identity = {"user": admin, "is_admin": True}

    @app.before_request
    def attach_actor():
        actor = identity["user"]
        if actor:
            g.user_id = actor.id
            g.user_email = actor.email
            g.user_data = {"display_name": actor.display_name}

    @contextmanager
    def test_db():
        try:
            yield db
        except Exception:
            db.rollback()
            raise

    monkeypatch.setattr(vehicle_checkouts, "get_db", test_db)
    monkeypatch.setattr(auth_middleware, "is_current_user_admin", lambda: identity["is_admin"])
    broadcasts = []
    monkeypatch.setattr(vehicle_checkouts.broadcast_dedup, "request_broadcast", broadcasts.append)
    with app.test_request_context():
        g.user_id = student.id
        g.user_email = student.email
        g.user_data = {"display_name": student.display_name}
        checkout = VehicleCheckoutService(db).checkout("golf_cart", checkout_type="other", purpose="Tech Duty", notes="Original note")
    try:
        yield app, db, checkout, student, admin, identity, broadcasts
    finally:
        db.close()
        engine.dispose()


def _request(checkout, **overrides):
    return {"vehicle": "golf_cart", "checkout_id": checkout.id, "reason": "Forgotten check-in", **overrides}


def test_force_return_preserves_student_and_audits_admin(recovery):
    app, db, checkout, student, admin, _, broadcasts = recovery
    started = checkout.checked_out_at
    response = app.test_client().post("/api/vehicle-checkouts/force-return", json=_request(checkout, reason="  Forgotten check-in  "))
    assert response.status_code == 200
    assert response.json["checked_in_at"] is not None
    assert checkout.checked_out_by_user_id == student.id
    assert checkout.checked_out_by == "Student One"
    assert checkout.checked_out_at == started
    assert checkout.checked_in_by_user_id == admin.id
    assert checkout.checked_in_by_email == admin.email
    assert checkout.notes.startswith("Original note")
    assert "[Admin force return] Forgotten check-in" in checkout.notes
    audit = db.query(SystemAuditLog).filter_by(action="force_returned").one()
    assert audit.user_id == admin.id
    assert audit.entity_id == checkout.id
    assert audit.audit_metadata["reason"] == "Forgotten check-in"
    assert audit.audit_metadata["checked_out_by_user_id"] == student.id
    assert len(broadcasts) == 1
    status = next(item for item in VehicleCheckoutService(db).get_vehicle_statuses() if item["vehicle"] == "golf_cart")
    assert status["checked_out"] is False


@pytest.mark.parametrize("order_status", [OrderStatus.IN_DELIVERY.value, OrderStatus.DELIVERED.value, None])
def test_active_run_is_blocked_even_if_delivered_or_empty(recovery, order_status):
    app, db, checkout, _, _, _, broadcasts = recovery
    run = DeliveryRun(id=str(uuid4()), name="Active Run", vehicle="golf_cart", runner="Student One", status=DeliveryRunStatus.ACTIVE.value)
    db.add(run)
    order = None
    if order_status:
        order = Order(inflow_order_id="FORCE-RETURN-TEST", status=order_status, delivery_run_id=run.id)
        db.add(order)
    db.commit()
    response = app.test_client().post(
        "/api/vehicle-checkouts/force-return",
        json=_request(checkout, allow_active_delivery_run=True),
    )
    assert response.status_code == 400
    assert response.json["error"]["details"]["delivery_run_active"] is True
    assert checkout.checked_in_at is None
    assert checkout.notes == "Original note"
    assert run.status == DeliveryRunStatus.ACTIVE.value
    if order:
        assert order.status == order_status
        assert order.delivery_run_id == run.id
    assert db.query(SystemAuditLog).count() == 0
    assert broadcasts == []


def test_completed_run_and_its_orders_are_unchanged(recovery):
    app, db, checkout, _, _, _, _ = recovery
    checkout.checkout_type = "delivery_run"
    run = DeliveryRun(id=str(uuid4()), name="Completed Run", vehicle="golf_cart", runner="Student One", status=DeliveryRunStatus.COMPLETED.value)
    order = Order(inflow_order_id="COMPLETED-TEST", status=OrderStatus.DELIVERED.value, delivery_run_id=run.id)
    db.add_all([run, order])
    db.commit()
    response = app.test_client().post("/api/vehicle-checkouts/force-return", json=_request(checkout))
    assert response.status_code == 200
    assert run.status == DeliveryRunStatus.COMPLETED.value
    assert order.status == OrderStatus.DELIVERED.value
    assert order.delivery_run_id == run.id


@pytest.mark.parametrize("authenticated,admin_access,status", [(False, False, 401), (True, False, 403)])
def test_force_return_requires_admin(recovery, authenticated, admin_access, status):
    app, db, checkout, student, _, identity, _ = recovery
    identity.update(user=student if authenticated else None, is_admin=admin_access)
    response = app.test_client().post("/api/vehicle-checkouts/force-return", json=_request(checkout))
    assert response.status_code == status
    assert checkout.checked_in_at is None
    assert db.query(SystemAuditLog).count() == 0


@pytest.mark.parametrize("override", [{"reason": "   "}, {"reason": "x" * 2001}, {"checkout_id": "invalid"}, {"vehicle": "pickup"}])
def test_invalid_force_return_request_is_rejected(recovery, override):
    app, _, checkout, _, _, _, _ = recovery
    response = app.test_client().post("/api/vehicle-checkouts/force-return", json=_request(checkout, **override))
    assert response.status_code == 400
    assert checkout.checked_in_at is None


def test_stale_confirmation_does_not_return_new_checkout(recovery):
    app, db, checkout, student, _, _, broadcasts = recovery
    old_request = _request(checkout)
    with app.test_request_context():
        g.user_id = student.id
        g.user_email = student.email
        g.user_data = {"display_name": student.display_name}
        service = VehicleCheckoutService(db)
        service.checkin("golf_cart")
        replacement = service.checkout("golf_cart", checkout_type="other", purpose="Tech Duty")
    response = app.test_client().post("/api/vehicle-checkouts/force-return", json=old_request)
    assert response.status_code == 409
    assert replacement.checked_in_at is None
    assert db.query(SystemAuditLog).count() == 0
    assert broadcasts == []


def test_repeat_force_return_is_rejected_without_extra_audit(recovery):
    app, db, checkout, _, _, _, _ = recovery
    client = app.test_client()
    body = _request(checkout)
    assert client.post("/api/vehicle-checkouts/force-return", json=body).status_code == 200
    assert client.post("/api/vehicle-checkouts/force-return", json=body).status_code == 400
    assert db.query(SystemAuditLog).count() == 1


def test_audit_failure_rolls_back_checkout(recovery, monkeypatch):
    app, db, checkout, _, _, _, broadcasts = recovery
    from app.services.audit_service import AuditService

    def fail_audit(*args, **kwargs):
        raise RuntimeError("Audit unavailable")

    monkeypatch.setattr(AuditService, "log_action", fail_audit)
    with pytest.raises(RuntimeError, match="Audit unavailable"):
        app.test_client().post("/api/vehicle-checkouts/force-return", json=_request(checkout))
    db.refresh(checkout)
    assert checkout.checked_in_at is None
    assert checkout.notes == "Original note"
    assert broadcasts == []
