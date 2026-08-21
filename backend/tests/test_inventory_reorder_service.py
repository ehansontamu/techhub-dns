import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

sys.path.append(".")

from app.services.inventory_reorder_service import (
    InventoryReorderService,
    compute_inventory_reorder_rows,
)


def test_compute_inventory_reorder_rows_flags_reorder_items_first():
    rows = compute_inventory_reorder_rows(
        [
            {
                "name": "Healthy Item",
                "sku": "OK1",
                "quantityAvailable": "20",
                "bigCommerceStatus9": "1",
                "quantityOnOrder": "0.00000",
                "reorderPoint": "5",
                "reorderQty": "10",
            },
            {
                "name": "Needs Item",
                "sku": "LOW1",
                "quantityAvailable": "4",
                "bigCommerceStatus9": "1",
                "quantityOnOrder": "1.00000",
                "reorderPoint": "4",
                "reorderQty": "12",
            },
        ]
    )

    assert [row["sku"] for row in rows] == ["LOW1", "OK1"]
    assert rows[0]["finalQty"] == 3
    assert rows[0]["combined"] == 4
    assert rows[0]["needsReorder"] is True
    assert rows[0]["critical"] is False
    assert rows[1]["needsReorder"] is False


def test_compute_inventory_reorder_rows_marks_negative_final_qty_critical():
    rows = compute_inventory_reorder_rows(
        [
            {
                "name": "Oversold Item",
                "sku": "BAD1",
                "quantityAvailable": "1",
                "bigCommerceStatus9": "3",
                "quantityOnOrder": "0",
                "reorderPoint": "5",
                "reorderQty": "10",
            }
        ]
    )

    assert rows[0]["finalQty"] == -2
    assert rows[0]["needsReorder"] is True
    assert rows[0]["critical"] is True


def test_compute_inventory_reorder_rows_hides_zero_reorder_quantity_by_default():
    rows = compute_inventory_reorder_rows(
        [
            {
                "name": "Not Reordered",
                "sku": "ZERO1",
                "quantityAvailable": "0",
                "bigCommerceStatus9": "0",
                "quantityOnOrder": "0",
                "reorderPoint": "0",
                "reorderQty": "0",
            }
        ]
    )
    all_rows = compute_inventory_reorder_rows(
        [
            {
                "name": "Not Reordered",
                "sku": "ZERO1",
                "quantityAvailable": "0",
                "bigCommerceStatus9": "0",
                "quantityOnOrder": "0",
                "reorderPoint": "0",
                "reorderQty": "0",
            }
        ],
        show_all=True,
    )

    assert rows == []
    assert all_rows[0]["sku"] == "ZERO1"


def test_compute_inventory_reorder_rows_removes_fulfilled_cached_inflow_orders():
    rows = compute_inventory_reorder_rows(
        [
            {
                "name": "Laptop",
                "sku": "LT-1",
                "quantityAvailable": "10",
                "bigCommerceStatus9": "0",
                "quantityOnOrder": "0",
                "reorderPoint": "5",
                "reorderQty": "10",
                "orders": {
                    "bigCommerce": [],
                    "inflow": [
                        {"orderNumber": "TH1", "status": "fulfilled"},
                        {"orderNumber": "TH2", "status": "started"},
                    ],
                },
            }
        ]
    )

    assert [order["orderNumber"] for order in rows[0]["orders"]["inflow"]] == ["TH2"]


def test_build_simple_summary_preserves_order_details_by_source():
    service = InventoryReorderService(SimpleNamespace())

    summary = service._build_simple_summary(
        [
            {
                "productId": "product-1",
                "name": "Laptop",
                "sku": "LT-1",
                "summary": {"quantityAvailable": "20", "quantityOnOrder": "3"},
                "reorderSettings": [],
            }
        ],
        {"LAPTOP": 12},
        bigcommerce_order_details={
            "LAPTOP": [
                {
                    "orderId": "901",
                    "orderNumber": "901",
                    "quantity": 12,
                    "status": "Aggiebuy Approval (Status 9)",
                }
            ]
        },
        inflow_order_details={
            "product-1": [
                {
                    "orderId": "inflow-1",
                    "orderNumber": "TH1001",
                    "quantity": 3,
                    "status": "started",
                }
            ]
        },
    )

    assert summary[0]["bigCommerceStatus9"] == "12"
    assert summary[0]["orders"]["bigCommerce"][0]["quantity"] == 12
    assert summary[0]["orders"]["inflow"][0]["orderNumber"] == "TH1001"


def test_inflow_order_details_exclude_fulfilled_orders_and_preserve_guid():
    service = InventoryReorderService(
        SimpleNamespace(
            inflow_api_url="https://inflow.example.test",
            inflow_company_id="company-1",
            inventory_reorder_request_delay_seconds=0,
        )
    )

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    responses = [
        FakeResponse(
            [
                {
                    "salesOrderId": "fulfilled-guid",
                    "orderNumber": "TH1000",
                    "inventoryStatus": "fulfilled",
                    "lines": [
                        {
                            "productId": "product-1",
                            "quantity": {"standardQuantity": "15"},
                        }
                    ],
                },
                {
                    "salesOrderId": "active-guid",
                    "orderNumber": "TH1001",
                    "inventoryStatus": "started",
                    "lines": [
                        {
                            "productId": "product-1",
                            "quantity": {"standardQuantity": "3"},
                        }
                    ],
                },
            ]
        ),
        FakeResponse([]),
    ]

    fake_requests = SimpleNamespace(get=lambda *_args, **_kwargs: responses.pop(0))
    with patch.dict(sys.modules, {"requests": fake_requests}):
        details = service._fetch_inflow_active_order_details(
            {"Authorization": "Bearer test"},
            progress=lambda _message, _pct: None,
        )

    assert len(details["product-1"]) == 1
    assert details["product-1"][0]["orderId"] == "active-guid"
    assert details["product-1"][0]["orderNumber"] == "TH1001"


def test_inventory_reorder_refresh_cooldown_uses_latest_start_time():
    service = InventoryReorderService(
        SimpleNamespace(inventory_reorder_refresh_cooldown_seconds=180)
    )
    started_at = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    service._jobs["job-1"] = {"started_at": started_at}

    cooldown = service.get_refresh_cooldown()

    assert cooldown["active"] is True
    assert 0 < cooldown["remaining_seconds"] <= 120


def test_inventory_reorder_latest_job_reads_persisted_metadata(tmp_path):
    service = InventoryReorderService(
        SimpleNamespace(
            inventory_reorder_refresh_cooldown_seconds=180,
            storage_root=str(tmp_path),
        )
    )
    metadata_path = tmp_path / "inventory-reorder" / "inventory_summary_metadata.json"
    metadata_path.parent.mkdir(parents=True)
    metadata_path.write_text(
        """
{
  "latest_job": {
    "job_id": "scheduled-job",
    "status": "done",
    "progress": 1.0,
    "message": "Refresh complete",
    "error": null,
    "started_at": "2026-07-30T17:00:00Z",
    "finished_at": "2026-07-30T17:05:00Z",
    "result_path": "storage/inventory-reorder/inventory_summary_simple.json",
    "trigger": "scheduled"
  },
  "updated_at": "2026-07-30T17:05:00Z"
}
""".strip(),
        encoding="utf-8",
    )

    latest = service.latest_job()

    assert latest is not None
    assert latest["job_id"] == "scheduled-job"
    assert latest["trigger"] == "scheduled"


def test_latest_summary_path_is_absolute_for_file_downloads(tmp_path):
    service = InventoryReorderService(SimpleNamespace(storage_root=str(tmp_path)))
    summary_path = tmp_path / "inventory-reorder" / "inventory_summary_simple.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("[]\n", encoding="utf-8")

    latest_path = service.latest_summary_path()

    assert latest_path == summary_path.resolve()
    assert latest_path.is_absolute()


def test_latest_summary_counts_ten_plus_bc_items_regardless_of_reorder_settings(tmp_path):
    service = InventoryReorderService(
        SimpleNamespace(
            storage_root=str(tmp_path),
            inventory_reorder_refresh_cooldown_seconds=0,
            inventory_reorder_scheduled_refresh_enabled=False,
            inventory_reorder_scheduled_refresh_times="7:30",
            inventory_reorder_scheduled_refresh_timezone="America/Chicago",
            inflow_api_key="test",
            inventory_reorder_bigcommerce_token="test",
            inflow_company_id="company",
            inventory_reorder_location_id="location",
            inventory_reorder_bigcommerce_store_id="store",
        )
    )
    summary_path = tmp_path / "inventory-reorder" / "inventory_summary_simple.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        """
[
  {
    "name": "Bulk Item",
    "sku": "BULK1",
    "quantityAvailable": "20",
    "quantityOnOrder": "0",
    "bigCommerceStatus9": "12",
    "reorderPoint": "0",
    "reorderQty": "0",
    "orders": {
      "bigCommerce": [{"orderId": "500", "orderNumber": "500", "quantity": 12}],
      "inflow": []
    }
  }
]
""".strip(),
        encoding="utf-8",
    )

    result = service.get_latest_summary(show_all=False)

    assert result["rows"] == []
    assert result["summary"]["ten_plus_bc_order_items"] == 1


def test_new_high_quantity_bigcommerce_orders_notify_once_after_baseline(tmp_path, monkeypatch):
    service = InventoryReorderService(
        SimpleNamespace(
            storage_root=str(tmp_path),
            inventory_reorder_teams_notifications_enabled=True,
            inventory_reorder_teams_recipient_email="inventory@example.com",
            inventory_reorder_teams_recipient_name="Inventory Team",
            inventory_reorder_teams_minimum_order_quantity=10,
        )
    )
    notifications = []

    def fake_send(**kwargs):
        notifications.append(kwargs)
        return True

    monkeypatch.setattr(service, "_send_bigcommerce_order_alert", fake_send)
    existing_order = {"id": "100", "products": [{"name": "Existing", "quantity": 12}]}
    new_order = {"id": "101", "products": [{"name": "New", "quantity": 10}]}

    service._notify_new_high_quantity_bigcommerce_orders([existing_order])
    service._notify_new_high_quantity_bigcommerce_orders([existing_order, new_order])
    service._notify_new_high_quantity_bigcommerce_orders([existing_order, new_order])

    assert len(notifications) == 1
    assert notifications[0]["bigcommerce_order_id"] == "101"
    assert notifications[0]["total_quantity"] == 10


def test_merge_bigcommerce_orders_deduplicates_status_and_recent_results():
    merged = InventoryReorderService._merge_bigcommerce_orders(
        [{"id": "100", "products": [{"name": "Status 9", "quantity": 10}]}],
        [
            {"id": "100", "products": [{"name": "Duplicate", "quantity": 10}]},
            {"id": "101", "products": [{"name": "Approved", "quantity": 10}]},
        ],
    )

    assert [order["id"] for order in merged] == ["100", "101"]


if __name__ == "__main__":
    test_compute_inventory_reorder_rows_flags_reorder_items_first()
    test_compute_inventory_reorder_rows_marks_negative_final_qty_critical()
    test_compute_inventory_reorder_rows_hides_zero_reorder_quantity_by_default()
    test_compute_inventory_reorder_rows_removes_fulfilled_cached_inflow_orders()
    test_build_simple_summary_preserves_order_details_by_source()
    test_inflow_order_details_exclude_fulfilled_orders_and_preserve_guid()
    test_inventory_reorder_refresh_cooldown_uses_latest_start_time()
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        test_inventory_reorder_latest_job_reads_persisted_metadata(Path(tmp))
        test_latest_summary_path_is_absolute_for_file_downloads(Path(tmp))
        test_latest_summary_counts_ten_plus_bc_items_regardless_of_reorder_settings(Path(tmp))
    print("[PASS] inventory reorder service tests passed")
