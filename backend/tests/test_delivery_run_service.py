#!/usr/bin/env python3
"""Focused tests for DeliveryRunService fulfillment behavior."""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch


backend_path = Path(__file__).parent.parent
sys.path.append(str(backend_path))

from app.services.delivery_run_service import DeliveryRunService


def test_fulfill_orders_persists_updated_inflow_payload():
    """The run service should persist the updated InFlow payload onto the local order."""

    order = SimpleNamespace(
        id="order-1",
        inflow_order_id="TH1001",
        inflow_sales_order_id="sales-order-1",
        inflow_data={"orderNumber": "TH1001", "packLines": []},
    )
    updated_payload = {
        "id": "sales-order-1",
        "orderNumber": "TH1001",
        "packLines": [{"productId": "prod-1"}],
        "shipLines": [{"salesOrderShipLineId": "ship-1"}],
    }

    service = DeliveryRunService(db=cast(Any, object()))

    with patch("app.services.delivery_run_service.InflowService") as inflow_service_cls:
        inflow_service_cls.return_value.fulfill_sales_order = AsyncMock(
            return_value=updated_payload
        )

        successes, failures = service._fulfill_orders_in_inflow(
            cast(Any, [order]), user_id="user-1"
        )

    assert failures == []
    assert len(successes) == 1
    assert successes[0]["inflow_sales_order_id"] == "sales-order-1"
    assert order.inflow_data == updated_payload
    print("[PASS] DeliveryRunService persists updated InFlow payload")


def test_fulfill_orders_accepts_already_fulfilled_inflow_order():
    """Retrying run completion should succeed when InFlow already fulfilled the order."""

    order = SimpleNamespace(
        id="order-fulfilled",
        inflow_order_id="TH2001",
        inflow_sales_order_id="sales-order-fulfilled",
        inflow_data={"orderNumber": "TH2001", "packLines": []},
    )
    fulfilled_payload = {
        "id": "sales-order-fulfilled",
        "orderNumber": "TH2001",
        "inventoryStatus": "fulfilled",
        "pickLines": [{"productId": "prod-1"}],
        "packLines": [{"productId": "prod-1"}],
        "shipLines": [{"salesOrderShipLineId": "ship-1"}],
    }

    service = DeliveryRunService(db=cast(Any, object()))

    with patch("app.services.delivery_run_service.InflowService") as inflow_service_cls:
        inflow_service = inflow_service_cls.return_value
        inflow_service.fulfill_sales_order = AsyncMock(
            side_effect=ValueError("Order TH2001 has no newly picked items to fulfill in InFlow")
        )
        inflow_service.get_order_by_id = AsyncMock(return_value=fulfilled_payload)

        successes, failures = service._fulfill_orders_in_inflow(
            cast(Any, [order]), user_id="user-1"
        )

    assert failures == []
    assert len(successes) == 1
    assert successes[0]["already_fulfilled"] is True
    assert successes[0]["inflow_sales_order_id"] == "sales-order-fulfilled"
    assert order.inflow_data == fulfilled_payload
    print("[PASS] DeliveryRunService accepts already-fulfilled InFlow retries")


def test_fulfill_orders_preserves_unconfirmed_inflow_failure():
    """A fulfillment error should still fail if InFlow does not confirm completion."""

    order = SimpleNamespace(
        id="order-unfulfilled",
        inflow_order_id="TH2002",
        inflow_sales_order_id="sales-order-unfulfilled",
        inflow_data={"orderNumber": "TH2002", "packLines": []},
    )
    unfulfilled_payload = {
        "id": "sales-order-unfulfilled",
        "orderNumber": "TH2002",
        "inventoryStatus": "started",
        "pickLines": [{"productId": "prod-1"}],
        "packLines": [],
        "shipLines": [],
    }

    service = DeliveryRunService(db=cast(Any, object()))

    with patch("app.services.delivery_run_service.InflowService") as inflow_service_cls:
        inflow_service = inflow_service_cls.return_value
        inflow_service.fulfill_sales_order = AsyncMock(
            side_effect=RuntimeError("InFlow API unavailable")
        )
        inflow_service.get_order_by_id = AsyncMock(return_value=unfulfilled_payload)

        successes, failures = service._fulfill_orders_in_inflow(
            cast(Any, [order]), user_id="user-1"
        )

    assert successes == []
    assert len(failures) == 1
    assert failures[0]["error"] == "InFlow API unavailable"
    assert order.inflow_data != unfulfilled_payload
    print("[PASS] DeliveryRunService preserves unconfirmed InFlow failures")


def test_requeue_partial_delivery_returns_order_to_pre_delivery():
    """Partial deliveries should reuse the original order and restart prep."""

    order = SimpleNamespace(
        id="order-2",
        inflow_order_id="TH1002",
        status="delivered",
        assigned_deliverer="runner-1",
        delivery_run_id="run-1",
        delivery_sequence=4,
        tagged_at="2026-03-20T10:00:00Z",
        tagged_by="tech-1",
        tag_data={"tag_ids": ["TAG-1"], "tag_request_status": "sent"},
        picklist_generated_at="2026-03-20T10:05:00Z",
        picklist_generated_by="tech-1",
        picklist_path="storage/picklists/TH1002.pdf",
        qa_completed_at="2026-03-20T10:10:00Z",
        qa_completed_by="tech-1",
        qa_data={"method": "Delivery"},
        qa_path="storage/qa/TH1002.pdf",
        qa_method="Delivery",
        signature_captured_at="2026-03-20T11:00:00Z",
        signed_picklist_path="storage/picklists/TH1002-signed.pdf",
        order_details_path="storage/order_details/TH1002.pdf",
        order_details_generated_at="2026-03-20T10:05:30Z",
        updated_at=None,
        inflow_data={
            "lines": [
                {
                    "productId": "prod-1",
                    "description": "Dock",
                    "quantity": {"standardQuantity": "3"},
                }
            ],
            "packLines": [
                {
                    "productId": "prod-1",
                    "quantity": {"standardQuantity": "1"},
                }
            ],
        },
    )

    service = DeliveryRunService(db=cast(Any, SimpleNamespace(add=lambda _item: None)))
    audit_service = cast(Any, SimpleNamespace(log_order_action=lambda **_kwargs: None))

    results = service._requeue_partially_delivered_orders(
        cast(Any, [order]), user_id="user-2", audit_service=audit_service
    )

    assert results["requeued_count"] == 1
    assert results["orders_requeued"][0]["inflow_order_id"] == "TH1002"
    assert results["orders_requeued"][0]["status"] == "picked"
    assert order.status == "picked"
    assert order.assigned_deliverer is None
    assert order.delivery_run_id is None
    assert order.delivery_sequence is None
    assert order.tagged_at is None
    assert order.tagged_by is None
    assert order.tag_data is None
    assert order.picklist_generated_at is None
    assert order.picklist_generated_by is None
    assert order.picklist_path is None
    assert order.qa_completed_at is None
    assert order.qa_completed_by is None
    assert order.qa_data is None
    assert order.qa_path is None
    assert order.qa_method is None
    assert order.signature_captured_at is None
    assert order.signed_picklist_path is None
    assert order.order_details_path is None
    assert order.order_details_generated_at is None
    assert order.updated_at is not None
    print(
        "[PASS] Partial deliveries return the original order to Picked and reset prep"
    )


if __name__ == "__main__":
    print("Running DeliveryRunService tests...")
    print()

    test_fulfill_orders_persists_updated_inflow_payload()
    test_fulfill_orders_accepts_already_fulfilled_inflow_order()
    test_fulfill_orders_preserves_unconfirmed_inflow_failure()
    test_requeue_partial_delivery_returns_order_to_pre_delivery()

    print()
    print("[SUCCESS] All DeliveryRunService tests passed!")
