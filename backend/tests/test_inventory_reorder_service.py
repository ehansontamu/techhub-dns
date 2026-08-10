import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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


if __name__ == "__main__":
    test_compute_inventory_reorder_rows_flags_reorder_items_first()
    test_compute_inventory_reorder_rows_marks_negative_final_qty_critical()
    test_compute_inventory_reorder_rows_hides_zero_reorder_quantity_by_default()
    test_inventory_reorder_refresh_cooldown_uses_latest_start_time()
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        test_inventory_reorder_latest_job_reads_persisted_metadata(Path(tmp))
        test_latest_summary_path_is_absolute_for_file_downloads(Path(tmp))
    print("[PASS] inventory reorder service tests passed")
