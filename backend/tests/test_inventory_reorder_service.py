import json
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

sys.path.append(".")

from app.services.inventory_reorder_service import (
    InventoryReorderService,
    _parse_recipient_emails,
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


def test_job_status_is_shared_between_scheduler_and_web_processes(tmp_path):
    settings = SimpleNamespace(storage_root=str(tmp_path))
    scheduler = InventoryReorderService(settings)
    web_worker = InventoryReorderService(settings)

    def refresh(job_id):
        running = web_worker.latest_job()
        assert running["job_id"] == job_id
        assert running["status"] == "running"
        assert web_worker.get_job(job_id) == running
        return tmp_path / "summary.json"

    with patch.object(scheduler, "_refresh_inventory", refresh):
        completed = scheduler.run_refresh_sync(trigger="scheduled")

    assert completed["status"] == "done"
    assert web_worker.get_job(completed["job_id"]) == completed
    assert web_worker.get_job("unknown-job") is None
    assert web_worker._jobs == {}


def test_latest_job_prefers_new_scheduled_run_over_old_worker_result(tmp_path):
    settings = SimpleNamespace(storage_root=str(tmp_path))
    web_worker = InventoryReorderService(settings)
    scheduler = InventoryReorderService(settings)
    old_job = {
        "job_id": "old-manual-job", "status": "done", "result_path": "old.json",
        "started_at": "2026-10-12T12:30:00Z", "finished_at": "2026-10-12T12:35:00Z",
    }
    web_worker._jobs[old_job["job_id"]] = old_job
    web_worker._latest_job_id = old_job["job_id"]
    scheduled_job = {
        "job_id": "new-scheduled-job", "status": "running", "progress": 0.01,
        "started_at": "2026-10-12T17:00:00Z", "finished_at": None,
    }
    scheduler._jobs[scheduled_job["job_id"]] = scheduled_job
    scheduler._write_latest_metadata_from_job(scheduled_job["job_id"])

    assert web_worker.latest_job() == scheduled_job
    assert web_worker.get_job(old_job["job_id"]) == old_job

    scheduler._update_job(scheduled_job["job_id"], progress=0.5, message="Loading orders")
    scheduler._write_latest_metadata_from_job(scheduled_job["job_id"])
    updated = web_worker.get_job(scheduled_job["job_id"])
    assert updated["progress"] == 0.5
    assert updated["message"] == "Loading orders"


def test_metadata_write_failure_preserves_previous_status(tmp_path):
    service = InventoryReorderService(SimpleNamespace(storage_root=str(tmp_path)))
    service._jobs["job-1"] = {"job_id": "job-1", "status": "running"}
    service._write_latest_metadata_from_job("job-1")
    previous = service._metadata_path().read_text(encoding="utf-8")
    service._update_job("job-1", status="done")

    with patch.object(json, "dump", side_effect=OSError("Interrupted write")):
        service._write_latest_metadata_from_job("job-1")

    assert service._metadata_path().read_text(encoding="utf-8") == previous
    assert list(service._data_dir().glob("*.tmp")) == []


def test_failed_refresh_status_is_shared_with_other_workers(tmp_path):
    settings = SimpleNamespace(storage_root=str(tmp_path))
    scheduler = InventoryReorderService(settings)
    web_worker = InventoryReorderService(settings)

    with patch.object(scheduler, "_refresh_inventory", side_effect=RuntimeError("Upstream unavailable")):
        failed = scheduler.run_refresh_sync(trigger="scheduled")

    assert failed["status"] == "error"
    assert failed["error"] == "Upstream unavailable"
    assert failed["finished_at"] is not None
    assert web_worker.get_job(failed["job_id"]) == failed
    assert web_worker.latest_job() == failed


def test_refresh_publishes_progress_and_summary_without_external_calls(tmp_path):
    settings = SimpleNamespace(
        storage_root=str(tmp_path),
        inventory_reorder_refresh_cooldown_seconds=0,
        inflow_api_key="test", inflow_company_id="test-company",
        inventory_reorder_bigcommerce_token="test",
        inventory_reorder_bigcommerce_store_id="test-store",
        inventory_reorder_location_id="test-location",
    )
    scheduler = InventoryReorderService(settings)
    web_worker = InventoryReorderService(settings)

    def fetch_products(_headers, *, progress):
        running = web_worker.latest_job()
        assert running["status"] == "running"
        assert running["message"] == "Fetching InFlow products..."
        assert running["progress"] == 0.08
        progress("Fetched product page", 0.2)
        assert web_worker.get_job(running["job_id"])["progress"] == 0.2
        return []

    with (
        patch.object(scheduler, "_fetch_all_products", side_effect=fetch_products),
        patch.object(scheduler, "_fetch_all_summaries", return_value=[]),
        patch.object(scheduler, "_fetch_bigcommerce_status9_demand", return_value=({}, {}, [])),
        patch.object(scheduler, "_fetch_recent_bigcommerce_orders", return_value=[]),
        patch.object(scheduler, "_fetch_inflow_active_order_details", return_value={}),
        patch.object(scheduler, "_notify_new_high_quantity_bigcommerce_orders") as notify,
    ):
        completed = scheduler.run_refresh_sync(trigger="scheduled")

    assert completed["status"] == "done"
    assert web_worker.get_job(completed["job_id"]) == completed
    assert web_worker.get_latest_summary()["latest_job"] == completed
    assert web_worker.get_latest_summary()["has_data"] is True
    notify.assert_called_once_with([])


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
        ),
        recipient_email_resolver=_parse_recipient_emails,
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


def test_new_bigcommerce_order_with_ten_total_units_across_items_does_not_notify(
    tmp_path, monkeypatch
):
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
    mixed_quantity_order = {
        "id": "101",
        "products": [
            {"name": "Product A", "quantity": 6},
            {"name": "Product B", "quantity": 4},
        ],
    }

    service._notify_new_high_quantity_bigcommerce_orders([existing_order])
    service._notify_new_high_quantity_bigcommerce_orders(
        [existing_order, mixed_quantity_order]
    )

    assert notifications == []


def test_new_high_quantity_bigcommerce_orders_notify_each_configured_recipient(tmp_path, monkeypatch):
    service = InventoryReorderService(
        SimpleNamespace(
            storage_root=str(tmp_path),
            inventory_reorder_teams_notifications_enabled=True,
            inventory_reorder_teams_recipient_email=(
                "first@example.com, second@example.com, FIRST@example.com"
            ),
            inventory_reorder_teams_recipient_name="Inventory Team",
            inventory_reorder_teams_minimum_order_quantity=10,
        ),
        recipient_email_resolver=_parse_recipient_emails,
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

    assert [notification["recipient_email"] for notification in notifications] == [
        "first@example.com",
        "second@example.com",
    ]


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
    for test in (
        test_job_status_is_shared_between_scheduler_and_web_processes,
        test_latest_job_prefers_new_scheduled_run_over_old_worker_result,
        test_metadata_write_failure_preserves_previous_status,
        test_failed_refresh_status_is_shared_with_other_workers,
        test_refresh_publishes_progress_and_summary_without_external_calls,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            test(Path(tmp))
    print("[PASS] inventory reorder service tests passed")
