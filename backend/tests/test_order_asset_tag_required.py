#!/usr/bin/env python3
"""Regression tests for asset-tag requirements in order serialization."""

import os
import sys
from datetime import datetime
from uuid import uuid4
from types import SimpleNamespace
from contextlib import contextmanager
from unittest.mock import patch

from flask import Flask

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
sys.path.append(".")

from app.api.routes import orders as orders_routes
from app.models.order import OrderStatus
from app.services.inflow_service import InflowService


def _make_order():
    now = datetime.utcnow()
    return SimpleNamespace(
        id=str(uuid4()),
        inflow_order_id="TH1001",
        inflow_sales_order_id=None,
        recipient_name="Test Recipient",
        recipient_contact=None,
        delivery_location="Building 101",
        po_number=None,
        status=OrderStatus.PICKED.value,
        assigned_deliverer=None,
        issue_reason=None,
        tagged_at=None,
        tagged_by=None,
        tag_data=None,
        picklist_generated_at=None,
        picklist_generated_by=None,
        picklist_path=None,
        delivery_run_id=None,
        delivery_sequence=None,
        qa_completed_at=None,
        qa_completed_by=None,
        qa_data=None,
        qa_path=None,
        qa_method=None,
        signature_captured_at=None,
        signed_picklist_path=None,
        bundle_path=None,
        order_details_path=None,
        order_details_generated_at=None,
        order_details_email_status=None,
        order_details_email_status_updated_at=None,
        shipping_workflow_status=None,
        shipping_workflow_status_updated_at=None,
        shipping_workflow_status_updated_by=None,
        shipped_to_carrier_at=None,
        shipped_to_carrier_by=None,
        carrier_name=None,
        tracking_number=None,
        parent_order_id=None,
        has_remainder=None,
        remainder_order_id=None,
        created_at=now,
        updated_at=now,
        inflow_data={
            "lines": [
                {
                    "productId": "prod-1",
                    "unitPrice": 149.99,
                    "quantity": {"standardQuantity": 1},
                }
            ]
        },
    )


@contextmanager
def _fake_db_context():
    yield SimpleNamespace()


def test_order_list_serializer_includes_asset_tag_required_false():
    order = _make_order()

    class _FakeInflowService:
        def requires_asset_tags_cached(self, _order, _cache):
            return False

    data = orders_routes._serialize_order_list_item(
        order,
        inflow_service=_FakeInflowService(),
        asset_tag_requirement_cache={},
    )

    assert data["asset_tag_required"] is False


def test_order_response_json_includes_asset_tag_required_false():
    order = _make_order()

    class _FakeInflowService:
        def requires_asset_tags(self, _order):
            return False

    app = Flask(__name__)
    with app.app_context():
        with patch.object(orders_routes, "InflowService", return_value=_FakeInflowService()):
            data = orders_routes._order_response_json(order)

    assert data["asset_tag_required"] is False


def test_order_response_json_uses_partial_pick_view_for_asset_tag_requirement():
    order = _make_order()
    order.inflow_data = {
        "lines": [
            {
                "productId": "prod-tagged",
                "product": {
                    "name": "Dell Pro Max 14 Premium",
                    "category": {"name": "Laptops Dell"},
                },
                "unitPrice": 1500,
                "quantity": {"standardQuantity": 4},
            },
            {
                "productId": "prod-surge",
                "product": {
                    "name": "Outlet Surge Protector",
                    "category": {"name": "Accessories"},
                },
                "unitPrice": 25,
                "quantity": {"standardQuantity": 4},
            },
        ],
        "pickLines": [
            {
                "productId": "prod-surge",
                "product": {
                    "name": "Outlet Surge Protector",
                    "category": {"name": "Accessories"},
                },
                "unitPrice": 25,
                "quantity": {"standardQuantity": 4},
            }
        ],
        "packLines": [],
        "shipLines": [],
    }
    partial_pick_view = {
        "lines": [
            {
                "productId": "prod-surge",
                "product": {
                    "name": "Outlet Surge Protector",
                    "category": {"name": "Accessories"},
                },
                "unitPrice": 25,
                "quantity": {"standardQuantity": 4},
            }
        ],
        "pickLines": [
            {
                "productId": "prod-surge",
                "product": {
                    "name": "Outlet Surge Protector",
                    "category": {"name": "Accessories"},
                },
                "unitPrice": 25,
                "quantity": {"standardQuantity": 4},
            }
        ],
        "packLines": [],
        "shipLines": [],
    }

    class _FakeInflowService:
        def get_pick_status(self, current_order, include_services=False):
            assert include_services is False
            assert current_order == order.inflow_data or current_order == partial_pick_view
            return {
                "is_fully_picked": False,
                "total_ordered": 8,
                "total_picked": 4,
                "missing_items": [],
            }

        def requires_asset_tags(self, current_order):
            assert [line["productId"] for line in current_order["lines"]] == ["prod-surge"]
            return False

    class _FakeSplittingService:
        def __init__(self, db):
            self.db = db

        def _build_partial_leg_view(self, current_order):
            assert current_order == order.inflow_data
            return partial_pick_view

    app = Flask(__name__)
    with app.app_context():
        with patch.object(orders_routes, "InflowService", return_value=_FakeInflowService()):
            with patch.object(orders_routes, "OrderSplittingService", _FakeSplittingService):
                with patch.object(orders_routes, "object_session", return_value=SimpleNamespace()):
                    data = orders_routes._order_response_json(order)

    assert data["asset_tag_required"] is False


def test_order_response_json_uses_remainder_pick_status_for_split_parent():
    order = _make_order()
    order.has_remainder = "Y"
    order.remainder_order_id = "child-order-1"
    order.inflow_data = {
        "lines": [
            {
                "productId": "prod-1",
                "unitPrice": 149.99,
                "quantity": {"standardQuantity": 10},
            }
        ],
        "pickLines": [
            {
                "productId": "prod-1",
                "unitPrice": 149.99,
                "quantity": {"standardQuantity": 2},
            }
        ],
    }
    remainder_pick_source = {
        "lines": [
            {
                "productId": "prod-1",
                "unitPrice": 149.99,
                "quantity": {"standardQuantity": 8},
            }
        ],
        "pickLines": [],
    }

    class _FakeInflowService:
        def requires_asset_tags(self, _order):
            return False

        def get_pick_status(self, order):
            assert order == remainder_pick_source
            return {
                "is_fully_picked": False,
                "total_ordered": 8,
                "total_picked": 0,
                "missing_items": [
                    {
                        "product_id": "prod-1",
                        "product_name": "prod-1",
                        "ordered": 8,
                        "picked": 0,
                    }
                ],
            }

    class _FakeSplittingService:
        def __init__(self, db):
            self.db = db

        def build_parent_remainder_document_view(self, current_order):
            assert current_order is order
            return remainder_pick_source

        def build_parent_remainder_pick_status_source(self, current_order):
            assert current_order is order
            return remainder_pick_source

    app = Flask(__name__)
    with app.app_context():
        with patch.object(orders_routes, "InflowService", return_value=_FakeInflowService()):
            with patch.object(orders_routes, "OrderSplittingService", _FakeSplittingService):
                with patch.object(orders_routes, "object_session", return_value=SimpleNamespace()):
                    data = orders_routes._order_response_json(order, db_session=SimpleNamespace())

    assert data["pick_status"] == {
        "is_fully_picked": False,
        "total_ordered": 8,
        "total_picked": 0,
        "missing_items": [
            {
                "product_id": "prod-1",
                "product_name": "prod-1",
                "ordered": 8,
                "picked": 0,
            }
        ],
    }


def test_get_orders_route_includes_remainder_pick_status_for_picked_status(monkeypatch):
    order = _make_order()
    order.has_remainder = "Y"
    order.remainder_order_id = "child-order-1"
    order.inflow_data = {
        "lines": [
            {
                "productId": "prod-1",
                "unitPrice": 149.99,
                "quantity": {"standardQuantity": 10},
            }
        ],
        "pickLines": [
            {
                "productId": "prod-1",
                "unitPrice": 149.99,
                "quantity": {"standardQuantity": 2},
            }
        ],
    }
    remainder_pick_source = {
        "lines": [
            {
                "productId": "prod-1",
                "unitPrice": 149.99,
                "quantity": {"standardQuantity": 8},
            }
        ],
        "pickLines": [],
    }

    class _FakeOrderService:
        def __init__(self, db):
            self.db = db

        def get_orders(self, **_kwargs):
            return [order], 1

    class _FakeInflowService:
        def get_pick_status(self, current_order):
            assert current_order == remainder_pick_source
            return {
                "is_fully_picked": False,
                "total_ordered": 8,
                "total_picked": 0,
                "missing_items": [],
            }

    class _FakeSplittingService:
        def __init__(self, db):
            self.db = db

        def build_parent_remainder_pick_status_source(self, current_order):
            assert current_order is order
            return remainder_pick_source

    monkeypatch.setattr(orders_routes, "get_db", _fake_db_context)
    monkeypatch.setattr(orders_routes, "OrderService", _FakeOrderService)
    monkeypatch.setattr(orders_routes, "InflowService", lambda: _FakeInflowService())
    monkeypatch.setattr(orders_routes, "OrderSplittingService", _FakeSplittingService)
    monkeypatch.setattr(
        orders_routes,
        "_serialize_order_list_item",
        lambda order, pick_status_data=None, *_args, **_kwargs: {
            "id": order.id,
            "pick_status": pick_status_data,
        },
    )

    app = Flask(__name__)
    with app.test_request_context("/orders?status=picked", method="GET"):
        response = orders_routes.get_orders.__wrapped__()

    assert response.get_json() == {
        "items": [
            {
                "id": order.id,
                "pick_status": {
                    "is_fully_picked": False,
                    "total_ordered": 8,
                    "total_picked": 0,
                    "missing_items": [],
                },
            }
        ],
        "total": 1,
        "skip": 0,
        "limit": 100,
    }


def test_laptop_category_prefix_still_requires_asset_tags():
    service = InflowService()

    assert service._is_asset_tag_required_line("Laptops Dell", 1200) is True


if __name__ == "__main__":
    test_order_list_serializer_includes_asset_tag_required_false()
    test_order_response_json_includes_asset_tag_required_false()
    test_order_response_json_uses_remainder_pick_status_for_split_parent()
    test_get_orders_route_includes_remainder_pick_status_for_picked_status()
    test_laptop_category_prefix_still_requires_asset_tags()
    print("[SUCCESS] order asset tag regression tests passed")
