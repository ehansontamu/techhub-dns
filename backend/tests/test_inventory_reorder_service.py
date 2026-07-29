import sys

sys.path.append(".")

from app.services.inventory_reorder_service import compute_inventory_reorder_rows


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
