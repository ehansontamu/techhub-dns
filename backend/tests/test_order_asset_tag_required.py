#!/usr/bin/env python3
"""Regression tests for asset-tag requirements in order serialization."""

import os
import sys
from datetime import datetime
from uuid import uuid4
from types import SimpleNamespace
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
        order_details_path=None,
        order_details_generated_at=None,
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


def test_laptop_category_prefix_still_requires_asset_tags():
    service = InflowService()

    assert service._is_asset_tag_required_line("Laptops Dell", 1200) is True


if __name__ == "__main__":
    test_order_list_serializer_includes_asset_tag_required_false()
    test_order_response_json_includes_asset_tag_required_false()
    test_laptop_category_prefix_still_requires_asset_tags()
    print("[SUCCESS] order asset tag regression tests passed")
