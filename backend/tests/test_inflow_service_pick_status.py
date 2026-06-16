from app.services.inflow_service import InflowService


def test_service_lines_do_not_make_order_partially_picked():
    service = InflowService()
    order = {
        "lines": [
            {
                "productId": "laptop-1",
                "description": "Laptop",
                "quantity": {"standardQuantity": "1"},
            },
            {
                "productId": "computer-imaging",
                "description": "Computer Imaging",
                "product": {"itemType": "service", "name": "Computer Imaging"},
                "quantity": {"standardQuantity": "1"},
                "serviceCompleted": True,
            },
        ],
        "pickLines": [
            {
                "productId": "laptop-1",
                "description": "Laptop",
                "quantity": {"standardQuantity": "1"},
            }
        ],
        "packLines": [],
    }

    pick_status = service.get_pick_status(order)

    assert pick_status == {
        "is_fully_picked": True,
        "total_ordered": 2,
        "total_picked": 2,
        "missing_items": [],
    }
    assert service._is_fully_picked(order) is True


def test_completed_computer_imaging_line_is_excluded_from_remaining_view():
    service = InflowService()
    order = {
        "lines": [
            {
                "productId": "laptop-1",
                "description": "Laptop",
                "unitPrice": 1000,
                "quantity": {"standardQuantity": "1"},
            },
            {
                "productId": "computer-imaging",
                "description": "Computer Imaging",
                "product": {"itemType": "service", "name": "Computer Imaging"},
                "unitPrice": 0,
                "quantity": {"standardQuantity": "1"},
                "serviceCompleted": True,
            },
        ],
        "pickLines": [
            {
                "productId": "laptop-1",
                "description": "Laptop",
                "quantity": {"standardQuantity": "1"},
            }
        ],
        "packLines": [
            {
                "productId": "laptop-1",
                "quantity": {"standardQuantity": "1"},
            }
        ],
    }

    remaining = service.build_remaining_order_view(order)

    assert remaining["lines"] == []
    assert remaining["pickLines"] == []


def test_picklist_view_includes_service_lines_as_picked_items():
    service = InflowService()
    order = {
        "lines": [
            {
                "productId": "laptop-1",
                "description": "Laptop",
                "product": {"name": "Laptop", "sku": "LAP-1"},
                "quantity": {"standardQuantity": "1"},
            },
            {
                "productId": "computer-imaging",
                "description": "Computer Imaging",
                "product": {"itemType": "service", "name": "Computer Imaging"},
                "quantity": {"standardQuantity": "1"},
                "serviceCompleted": True,
            },
        ],
        "pickLines": [
            {
                "productId": "laptop-1",
                "description": "Laptop",
                "product": {"name": "Laptop", "sku": "LAP-1"},
                "quantity": {"standardQuantity": "1"},
            }
        ],
        "packLines": [],
    }

    picklist_view = service.build_picklist_view(order)

    assert [line["productId"] for line in picklist_view["pickLines"]] == [
        "laptop-1",
        "computer-imaging",
    ]
    assert picklist_view["pickLines"][1]["product"]["name"] == "Computer Imaging"
    assert picklist_view["pickLines"][1]["product"]["sku"] == "SERVICE"


def test_computer_imaging_counts_as_picked_even_without_service_completed():
    service = InflowService()
    order = {
        "lines": [
            {
                "productId": "laptop-1",
                "description": "Laptop",
                "quantity": {"standardQuantity": "1"},
            },
            {
                "productId": "computer-imaging",
                "description": "Computer Imaging",
                "product": {"itemType": "service", "name": "Computer Imaging"},
                "quantity": {"standardQuantity": "1"},
                "serviceCompleted": False,
            },
        ],
        "pickLines": [
            {
                "productId": "laptop-1",
                "description": "Laptop",
                "quantity": {"standardQuantity": "1"},
            }
        ],
        "packLines": [],
    }

    service_aware_status = service.get_pick_status(order)
    physical_status = service.get_pick_status(order, include_services=False)
    picklist_view = service.build_picklist_view(order)

    assert service_aware_status == {
        "is_fully_picked": True,
        "total_ordered": 2,
        "total_picked": 2,
        "missing_items": [],
    }
    assert physical_status == {
        "is_fully_picked": True,
        "total_ordered": 1,
        "total_picked": 1,
        "missing_items": [],
    }
    assert [line["productId"] for line in picklist_view["pickLines"]] == [
        "laptop-1",
        "computer-imaging",
    ]
