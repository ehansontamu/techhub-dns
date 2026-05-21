#!/usr/bin/env python3
"""Regression tests for order rollback behavior."""

import os
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
sys.path.append(".")

from app.models.order import OrderStatus, ShippingWorkflowStatus
from app.services.order_service import OrderService
from app.utils.exceptions import ValidationError


class FakeQuery:
    def __init__(self, order):
        self._order = order

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._order


class FakeDb:
    def __init__(self, order):
        self.order = order
        self.added = []
        self.committed = False
        self.refreshed = None

    def query(self, model):
        return FakeQuery(self.order)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        self.refreshed = obj


def build_order(status: OrderStatus):
    now = datetime(2026, 1, 1, 12, 0, 0)
    return SimpleNamespace(
        id="order-1",
        inflow_order_id="TH123",
        status=status.value,
        issue_reason="Needs review",
        delivery_run_id="run-1",
        delivery_sequence=2,
        shipping_workflow_status=ShippingWorkflowStatus.SHIPPED.value,
        shipping_workflow_status_updated_at=now,
        shipping_workflow_status_updated_by="shipper@example.com",
        shipped_to_carrier_at=now,
        shipped_to_carrier_by="shipper@example.com",
        carrier_name="UPS",
        tracking_number="1Z123",
        signature_captured_at=now,
        signed_picklist_path="/tmp/signed.pdf",
        assigned_deliverer="driver@example.com",
        qa_completed_at=now,
        qa_completed_by="qa@example.com",
        qa_data={"passed": True},
        qa_path="/tmp/qa.json",
        qa_method="shipping",
        tagged_at=now,
        tagged_by="tagger@example.com",
        tag_data={"tag_ids": ["A1"]},
        picklist_generated_at=now,
        picklist_generated_by="picker@example.com",
        picklist_path="/tmp/picklist.pdf",
        order_details_path="/tmp/order-details.pdf",
        order_details_generated_at=now,
        updated_at=now,
    )


def test_rollback_to_pre_delivery_clears_delivery_and_shipping_state():
    # Rollback now only allowed from ISSUE status (quarantine-first workflow)
    order = build_order(OrderStatus.ISSUE)
    db = FakeDb(order)
    service = OrderService(db)

    result = service.rollback_status(
        order_id=order.id,
        target_status=OrderStatus.PRE_DELIVERY,
        changed_by="ops@example.com",
        reason="Re-open for review",
    )

    assert result.status == OrderStatus.PRE_DELIVERY.value
    assert result.issue_reason is None
    assert result.delivery_run_id is None
    assert result.delivery_sequence is None
    assert result.shipping_workflow_status == ShippingWorkflowStatus.WORK_AREA.value
    assert result.shipping_workflow_status_updated_at is None
    assert result.shipping_workflow_status_updated_by is None
    assert result.shipped_to_carrier_at is None
    assert result.shipped_to_carrier_by is None
    assert result.carrier_name is None
    assert result.tracking_number is None
    assert result.signature_captured_at is None
    assert result.signed_picklist_path is None
    assert result.qa_completed_at == datetime(2026, 1, 1, 12, 0, 0)
    assert result.picklist_generated_at == datetime(2026, 1, 1, 12, 0, 0)
    assert db.committed is True
    assert db.refreshed is result
    assert len(db.added) == 2
    assert db.added[0].__class__.__name__ == "AuditLog"
    assert db.added[1].__class__.__name__ == "OrderStatusHistory"
    assert db.added[1].status_metadata["rollback"] is True
    assert "shipping_workflow_status_updated_at" in db.added[1].status_metadata["cleared_fields"]
    print("[PASS] rollback to pre-delivery clears downstream delivery state")


def test_rollback_to_qa_clears_qa_state():
    # Rollback now only allowed from ISSUE status (quarantine-first workflow)
    order = build_order(OrderStatus.ISSUE)
    db = FakeDb(order)
    service = OrderService(db)

    result = service.rollback_status(
        order_id=order.id,
        target_status=OrderStatus.QA,
        changed_by="ops@example.com",
        reason="Redo QA",
    )

    assert result.status == OrderStatus.QA.value
    assert result.assigned_deliverer is None
    assert result.qa_completed_at is None
    assert result.qa_completed_by is None
    assert result.qa_data is None
    assert result.qa_path is None
    assert result.qa_method is None
    assert result.tagged_at == datetime(2026, 1, 1, 12, 0, 0)
    assert result.picklist_path == "/tmp/picklist.pdf"
    print("[PASS] rollback to QA clears QA completion state")


def test_rollback_rejects_forward_transition():
    order = build_order(OrderStatus.PICKED)
    db = FakeDb(order)
    service = OrderService(db)

    with pytest.raises(ValidationError):
        service.rollback_status(
            order_id=order.id,
            target_status=OrderStatus.QA,
            changed_by="ops@example.com",
            reason="Invalid",
        )
    print("[PASS] rollback rejects forward status changes")


if __name__ == "__main__":
    test_rollback_to_pre_delivery_clears_delivery_and_shipping_state()
    test_rollback_to_qa_clears_qa_state()
    test_rollback_rejects_forward_transition()
    print("[SUCCESS] order rollback regression tests passed!")
