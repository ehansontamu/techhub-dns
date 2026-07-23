#!/usr/bin/env python3
"""Focused tests for DeliveryRunService fulfillment behavior."""

import asyncio
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from flask import Flask, g
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


backend_path = Path(__file__).parent.parent
sys.path.append(str(backend_path))

from app import models  # noqa: F401  # ensure all mapped models are registered
from app.database import Base
from app.models.audit_log import AuditLog, SystemAuditLog
from app.models.delivery_run import DeliveryRun, DeliveryRunStatus
from app.models.order import Order, OrderStatus
from app.models.order_status_history import OrderStatusHistory
from app.models.user import User
from app.services.delivery_run_service import DeliveryRunService
from app.utils.exceptions import ValidationError


def _make_delivery_run_session():
    temp_dir = tempfile.TemporaryDirectory()
    db_path = Path(temp_dir.name) / "delivery-run.sqlite"
    engine = create_engine(f"sqlite:///{db_path}")
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    Base.metadata.create_all(engine)
    return temp_dir, engine, testing_session


def test_append_orders_to_active_run_assigns_final_sequences_and_audits():
    temp_dir, engine, session_factory = _make_delivery_run_session()
    app = Flask(__name__)

    try:
        db = session_factory()
        user = User(
            id="user-1",
            tamu_oid="oid-1",
            email="runner@example.com",
            display_name="Runner One",
        )
        run = DeliveryRun(
            id="run-1",
            name="Run 1",
            runner="Runner One",
            vehicle="van",
            status=DeliveryRunStatus.ACTIVE.value,
            start_time=datetime.utcnow(),
        )
        existing_order = Order(
            id="existing-order",
            inflow_order_id="TH1",
            status=OrderStatus.IN_DELIVERY.value,
            delivery_run_id=run.id,
            delivery_sequence=4,
        )
        first_added = Order(
            id="first-added",
            inflow_order_id="TH9",
            status=OrderStatus.PRE_DELIVERY.value,
        )
        second_added = Order(
            id="second-added",
            inflow_order_id="TH10",
            status=OrderStatus.PRE_DELIVERY.value,
        )
        db.add_all([user, run, existing_order, first_added, second_added])
        db.commit()
        expected_updated_at = run.updated_at

        with app.test_request_context():
            g.user_id = user.id
            g.user_email = user.email
            g.user_data = {"display_name": user.display_name}
            updated_run = DeliveryRunService(db).append_orders_to_run(
                run.id,
                [first_added.id, second_added.id],
                expected_updated_at=expected_updated_at,
            )

        db.refresh(first_added)
        db.refresh(second_added)

        assert updated_run.status == DeliveryRunStatus.ACTIVE.value
        assert first_added.status == OrderStatus.IN_DELIVERY.value
        assert second_added.status == OrderStatus.IN_DELIVERY.value
        assert first_added.delivery_run_id == run.id
        assert second_added.delivery_run_id == run.id
        assert first_added.delivery_sequence == 5
        assert second_added.delivery_sequence == 6

        audit_rows = (
            db.query(AuditLog)
            .filter(AuditLog.order_id.in_([first_added.id, second_added.id]))
            .all()
        )
        assert len(audit_rows) == 2
        assert {row.changed_by for row in audit_rows} == {"Runner One"}

        history_rows = (
            db.query(OrderStatusHistory)
            .filter(
                OrderStatusHistory.order_id.in_([first_added.id, second_added.id])
            )
            .all()
        )
        assert len(history_rows) == 2
        assert {
            row.status_metadata["delivery_sequence"] for row in history_rows
        } == {5, 6}

        run_audit = (
            db.query(SystemAuditLog)
            .filter(
                SystemAuditLog.entity_type == "delivery_run",
                SystemAuditLog.entity_id == run.id,
                SystemAuditLog.action == "orders_added",
            )
            .one()
        )
        assert run_audit.audit_metadata["order_ids"] == [
            first_added.id,
            second_added.id,
        ]
    finally:
        db.close()
        engine.dispose()
        temp_dir.cleanup()


def test_append_orders_to_active_run_rejects_stale_ineligible_order():
    temp_dir, engine, session_factory = _make_delivery_run_session()
    app = Flask(__name__)

    try:
        db = session_factory()
        user = User(
            id="user-2",
            tamu_oid="oid-2",
            email="runner2@example.com",
            display_name="Runner Two",
        )
        run = DeliveryRun(
            id="run-2",
            name="Run 2",
            runner="Runner Two",
            vehicle="golf_cart",
            status=DeliveryRunStatus.ACTIVE.value,
        )
        stale_order = Order(
            id="stale-order",
            inflow_order_id="TH11",
            status=OrderStatus.IN_DELIVERY.value,
        )
        db.add_all([user, run, stale_order])
        db.commit()

        with app.test_request_context():
            g.user_id = user.id
            g.user_email = user.email
            g.user_data = {"display_name": user.display_name}

            try:
                DeliveryRunService(db).append_orders_to_run(
                    run.id,
                    [stale_order.id],
                )
                raise AssertionError("Expected an ineligible-order validation error")
            except ValidationError as exc:
                assert "no longer eligible" in str(exc)

        db.refresh(stale_order)
        assert stale_order.delivery_run_id is None
        assert stale_order.delivery_sequence is None
        assert stale_order.status == OrderStatus.IN_DELIVERY.value
    finally:
        db.close()
        engine.dispose()
        temp_dir.cleanup()


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
    assert (
        inflow_service_cls.return_value.fulfill_sales_order.await_args.kwargs[
            "shipment_tracking_number"
        ]
        is None
    )
    print("[PASS] DeliveryRunService persists updated InFlow payload")


def test_fulfill_orders_preserve_split_leg_snapshot_after_partial_success():
    """Split child legs should keep their local lines/picks after InFlow fulfillment."""

    order = SimpleNamespace(
        id="order-split-1",
        inflow_order_id="TH1004-P2",
        inflow_sales_order_id="sales-order-4",
        parent_order_id="parent-order-1",
        inflow_data={
            "lines": [{"productId": "prod-1", "quantity": {"standardQuantity": "1"}}],
            "pickLines": [{"productId": "prod-1", "quantity": {"standardQuantity": "1"}}],
            "packLines": [],
            "shipLines": [],
        },
    )
    updated_payload = {
        "orderNumber": "TH1004",
        "inventoryStatus": "started",
        "packLines": [{"productId": "prod-1", "quantity": {"standardQuantity": "3"}}],
        "shipLines": [{"salesOrderShipLineId": "ship-live"}],
        "_techhub_partial_leg_pack_lines": [
            {"productId": "prod-1", "quantity": {"standardQuantity": "1"}}
        ],
        "_techhub_partial_leg_ship_lines": [
            {"salesOrderShipLineId": "ship-leg-1"}
        ],
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
    assert order.inflow_data["lines"] == [
        {"productId": "prod-1", "quantity": {"standardQuantity": "1"}}
    ]
    assert order.inflow_data["pickLines"] == [
        {"productId": "prod-1", "quantity": {"standardQuantity": "1"}}
    ]
    assert order.inflow_data["packLines"] == [
        {"productId": "prod-1", "quantity": {"standardQuantity": "1"}}
    ]
    assert order.inflow_data["shipLines"] == [{"salesOrderShipLineId": "ship-leg-1"}]
    assert (
        inflow_service_cls.return_value.fulfill_sales_order.await_args.kwargs[
            "shipment_tracking_number"
        ]
        == service.PARTIAL_ORDER_TRACKING_NUMBER
    )
    print("[PASS] DeliveryRunService preserves split-leg snapshots after fulfillment")


def test_fulfill_orders_preserve_remainder_parent_snapshot_after_fulfillment():
    """A remainder parent keeps its leg-scoped lines when its own delivery is fulfilled."""

    order = SimpleNamespace(
        id="order-remainder-parent-1",
        inflow_order_id="TH1005",
        inflow_sales_order_id="sales-order-5",
        parent_order_id=None,
        remainder_order_id="child-order-1",
        has_remainder="Y",
        inflow_data={
            "lines": [{"productId": "prod-2", "quantity": {"standardQuantity": "2"}}],
            "pickLines": [{"productId": "prod-2", "quantity": {"standardQuantity": "2"}}],
            "packLines": [],
            "shipLines": [],
        },
    )
    # InFlow only knows the original combined order, so its response carries
    # the full item set (including the child leg that was already delivered).
    updated_payload = {
        "orderNumber": "TH1005",
        "inventoryStatus": "fulfilled",
        "lines": [
            {"productId": "prod-1", "quantity": {"standardQuantity": "1"}},
            {"productId": "prod-2", "quantity": {"standardQuantity": "2"}},
        ],
        "pickLines": [
            {"productId": "prod-1", "quantity": {"standardQuantity": "1"}},
            {"productId": "prod-2", "quantity": {"standardQuantity": "2"}},
        ],
        "packLines": [
            {"productId": "prod-1"},
            {"productId": "prod-2"},
        ],
        "shipLines": [{"salesOrderShipLineId": "ship-live"}],
        "_techhub_partial_leg_pack_lines": [
            {"productId": "prod-2", "quantity": {"standardQuantity": "2"}}
        ],
        "_techhub_partial_leg_ship_lines": [{"salesOrderShipLineId": "ship-leg-2"}],
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
    assert order.inflow_data["lines"] == [
        {"productId": "prod-2", "quantity": {"standardQuantity": "2"}}
    ]
    assert order.inflow_data["pickLines"] == [
        {"productId": "prod-2", "quantity": {"standardQuantity": "2"}}
    ]
    assert order.inflow_data["packLines"] == [
        {"productId": "prod-2", "quantity": {"standardQuantity": "2"}}
    ]
    assert order.inflow_data["shipLines"] == [{"salesOrderShipLineId": "ship-leg-2"}]
    assert (
        inflow_service_cls.return_value.fulfill_sales_order.await_args.kwargs[
            "shipment_tracking_number"
        ]
        == service.PARTIAL_ORDER_TRACKING_NUMBER
    )
    print("[PASS] DeliveryRunService preserves remainder-parent snapshots after fulfillment")


def test_fulfill_orders_already_fulfilled_preserves_split_snapshot():
    """The already-fulfilled retry path must not clobber split-leg item sets either."""

    order = SimpleNamespace(
        id="order-split-fulfilled",
        inflow_order_id="TH2002-P",
        inflow_sales_order_id="sales-order-split-fulfilled",
        parent_order_id="parent-order-2",
        remainder_order_id=None,
        has_remainder=None,
        inflow_data={
            "orderNumber": "TH2002-P",
            "lines": [{"productId": "prod-1", "quantity": {"standardQuantity": "1"}}],
            "pickLines": [{"productId": "prod-1", "quantity": {"standardQuantity": "1"}}],
            "packLines": [{"productId": "prod-1", "quantity": {"standardQuantity": "1"}}],
            "shipLines": [{"salesOrderShipLineId": "ship-leg-1"}],
        },
    )
    fulfilled_payload = {
        "id": "sales-order-split-fulfilled",
        "orderNumber": "TH2002",
        "inventoryStatus": "fulfilled",
        "lines": [
            {"productId": "prod-1", "quantity": {"standardQuantity": "1"}},
            {"productId": "prod-2", "quantity": {"standardQuantity": "4"}},
        ],
        "pickLines": [
            {"productId": "prod-1", "quantity": {"standardQuantity": "1"}},
            {"productId": "prod-2", "quantity": {"standardQuantity": "4"}},
        ],
        "packLines": [{"productId": "prod-1"}, {"productId": "prod-2"}],
        "shipLines": [{"salesOrderShipLineId": "ship-live"}],
    }

    service = DeliveryRunService(db=cast(Any, object()))

    with patch("app.services.delivery_run_service.InflowService") as inflow_service_cls:
        inflow_service = inflow_service_cls.return_value
        inflow_service.fulfill_sales_order = AsyncMock(
            side_effect=ValueError("Order TH2002 has no newly picked items to fulfill in InFlow")
        )
        inflow_service.get_order_by_id = AsyncMock(return_value=fulfilled_payload)

        successes, failures = service._fulfill_orders_in_inflow(
            cast(Any, [order]), user_id="user-1"
        )

    assert failures == []
    assert len(successes) == 1
    assert successes[0]["already_fulfilled"] is True
    assert order.inflow_data["lines"] == [
        {"productId": "prod-1", "quantity": {"standardQuantity": "1"}}
    ]
    assert order.inflow_data["pickLines"] == [
        {"productId": "prod-1", "quantity": {"standardQuantity": "1"}}
    ]
    assert order.inflow_data["packLines"] == [
        {"productId": "prod-1", "quantity": {"standardQuantity": "1"}}
    ]
    assert order.inflow_data["shipLines"] == [{"salesOrderShipLineId": "ship-leg-1"}]
    assert order.inflow_data["inventoryStatus"] == "fulfilled"
    print("[PASS] DeliveryRunService keeps split snapshots on already-fulfilled retries")


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


def test_fulfill_orders_limits_inflow_concurrency():
    """Delivery-run fulfillment should cap concurrent InFlow requests."""

    orders = [
        SimpleNamespace(
            id=f"order-{index}",
            inflow_order_id=f"TH30{index}",
            inflow_sales_order_id=f"sales-order-{index}",
            inflow_data={"orderNumber": f"TH30{index}", "packLines": []},
        )
        for index in range(6)
    ]

    service = DeliveryRunService(db=cast(Any, object()))
    max_in_flight = 0
    in_flight = 0

    async def fake_fulfill_sales_order(
        sales_order_id: str, **_kwargs: Any
    ) -> dict[str, Any]:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return {
            "id": sales_order_id,
            "orderNumber": sales_order_id,
            "packLines": [{"productId": "prod-1"}],
            "shipLines": [{"salesOrderShipLineId": f"ship-{sales_order_id}"}],
        }

    with patch("app.services.delivery_run_service.InflowService") as inflow_service_cls:
        inflow_service_cls.return_value.fulfill_sales_order = fake_fulfill_sales_order

        successes, failures = service._fulfill_orders_in_inflow(
            cast(Any, orders), user_id="user-1"
        )

    assert failures == []
    assert len(successes) == len(orders)
    assert max_in_flight == service.INFLOW_FULFILLMENT_CONCURRENCY
    print("[PASS] DeliveryRunService caps InFlow fulfillment concurrency")


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

    test_append_orders_to_active_run_assigns_final_sequences_and_audits()
    test_append_orders_to_active_run_rejects_stale_ineligible_order()
    test_fulfill_orders_persists_updated_inflow_payload()
    test_fulfill_orders_accepts_already_fulfilled_inflow_order()
    test_fulfill_orders_preserves_unconfirmed_inflow_failure()
    test_fulfill_orders_limits_inflow_concurrency()
    test_requeue_partial_delivery_returns_order_to_pre_delivery()

    print()
    print("[SUCCESS] All DeliveryRunService tests passed!")
