#!/usr/bin/env python3
"""Focused tests for multi-leg partial-order behavior."""

import asyncio
import os
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


backend_path = Path(__file__).parent.parent
sys.path.append(str(backend_path))

from app.database import Base
from app.models.order import Order, OrderStatus
from app.services.inflow_service import InflowService
from app.services.order_service import OrderService
from app.services.order_splitting import OrderSplittingService
from app.utils.exceptions import ValidationError


def test_fulfill_sales_order_appends_new_partial_shipment_lines():
    """A later partial-delivery leg should append new pack/ship lines in InFlow."""

    order_payload = {
        "salesOrderId": "sales-order-3",
        "orderNumber": "TH1003",
        "customerId": "customer-1",
        "lines": [
            {
                "productId": "prod-1",
                "description": "Laptop",
                "quantity": {"standardQuantity": "2"},
            }
        ],
        "pickLines": [
            {
                "productId": "prod-1",
                "description": "Laptop",
                "quantity": {"standardQuantity": "2"},
            }
        ],
        "packLines": [
            {
                "salesOrderPackLineId": "pack-1",
                "productId": "prod-1",
                "description": "Laptop",
                "containerNumber": "DELIVERY-TH1003-1",
                "quantity": {"standardQuantity": "1"},
            }
        ],
        "shipLines": [
            {
                "salesOrderShipLineId": "ship-1",
                "carrier": "TechHub",
                "containers": ["DELIVERY-TH1003-1"],
                "shippedDate": "2026-03-23T12:00:00Z",
            }
        ],
    }

    recorded: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"items": [recorded["payload"]]}

    class FakeAsyncClient:
        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def put(
            self, url: str, json: dict[str, Any], headers: dict[str, str]
        ) -> FakeResponse:
            recorded["url"] = url
            recorded["payload"] = json
            recorded["headers"] = headers
            return FakeResponse()

    service = InflowService()
    service._headers = {
        "Authorization": "Bearer test",
        "Content-Type": "application/json",
    }

    with patch.object(
        service, "get_order_by_id", AsyncMock(return_value=order_payload)
    ):
        with patch("app.services.inflow_service.httpx.AsyncClient", FakeAsyncClient):
            result = asyncio.run(
                service.fulfill_sales_order("sales-order-3", only_picked_items=True)
            )

    assert len(result["packLines"]) == 2
    assert result["packLines"][1]["containerNumber"] == "DELIVERY-TH1003-2"
    assert result["packLines"][1]["quantity"]["standardQuantity"] == "1.0"
    assert len(result["shipLines"]) == 2
    assert result["shipLines"][1]["containers"] == ["DELIVERY-TH1003-2"]
    print("[PASS] InFlow fulfillment appends a second partial-delivery shipment")


def test_fulfill_sales_order_uses_local_split_leg_snapshot_for_partial_delivery():
    """Split delivery legs should fulfill from the local leg snapshot, not cumulative live picks."""

    live_order_payload = {
        "salesOrderId": "sales-order-4",
        "orderNumber": "TH1004",
        "customerId": "customer-1",
        "lines": [
            {
                "productId": "prod-1",
                "description": "Laptop",
                "quantity": {"standardQuantity": "3"},
            }
        ],
        "pickLines": [
            {
                "productId": "prod-1",
                "description": "Laptop",
                "quantity": {"standardQuantity": "2"},
            }
        ],
        "packLines": [
            {
                "salesOrderPackLineId": "pack-1",
                "productId": "prod-1",
                "description": "Laptop",
                "containerNumber": "DELIVERY-TH1004-1",
                "quantity": {"standardQuantity": "2"},
            }
        ],
        "shipLines": [
            {
                "salesOrderShipLineId": "ship-1",
                "carrier": "TechHub",
                "containers": ["DELIVERY-TH1004-1"],
                "shippedDate": "2026-03-23T12:00:00Z",
            }
        ],
    }
    local_leg_snapshot = {
        "lines": [
            {
                "productId": "prod-1",
                "description": "Laptop",
                "quantity": {"standardQuantity": "1"},
            }
        ],
        "pickLines": [
            {
                "productId": "prod-1",
                "description": "Laptop",
                "quantity": {"standardQuantity": "1"},
            }
        ],
        "packLines": [],
        "shipLines": [],
    }

    recorded: dict[str, Any] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"items": [recorded["payload"]]}

    class FakeAsyncClient:
        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def put(
            self, url: str, json: dict[str, Any], headers: dict[str, str]
        ) -> FakeResponse:
            recorded["payload"] = json
            return FakeResponse()

    service = InflowService()
    service._headers = {
        "Authorization": "Bearer test",
        "Content-Type": "application/json",
    }

    with patch.object(
        service, "get_order_by_id", AsyncMock(return_value=live_order_payload)
    ):
        with patch("app.services.inflow_service.httpx.AsyncClient", FakeAsyncClient):
            result = asyncio.run(
                service.fulfill_sales_order(
                    "sales-order-4",
                    only_picked_items=True,
                    source_order_data=local_leg_snapshot,
                    source_order_identifier="TH1004-P2",
                )
            )

    assert len(recorded["payload"]["packLines"]) == 2
    assert recorded["payload"]["packLines"][1]["quantity"]["standardQuantity"] == "1"
    assert result["_techhub_partial_leg_pack_lines"][0]["quantity"]["standardQuantity"] == "1"
    assert result["_techhub_partial_leg_ship_lines"][0]["containers"] == ["DELIVERY-TH1004-2"]
    print("[PASS] Split partial-delivery legs use the local leg snapshot")


def test_asset_tag_serials_only_include_unshipped_remaining_items():
    """Asset-tag prep should only expose serials for the remaining leg."""

    service = InflowService()
    order_payload = {
        "lines": [
            {
                "productId": "prod-asset",
                "product": {"name": "Laptop", "category": {"name": "Laptops Dell"}},
                "unitPrice": 1200,
                "quantity": {
                    "standardQuantity": "2",
                    "serialNumbers": ["SN-1", "SN-2"],
                },
            }
        ],
        "pickLines": [
            {
                "productId": "prod-asset",
                "product": {"name": "Laptop", "category": {"name": "Laptops Dell"}},
                "unitPrice": 1200,
                "quantity": {
                    "standardQuantity": "2",
                    "serialNumbers": ["SN-1", "SN-2"],
                },
            }
        ],
        "packLines": [
            {
                "productId": "prod-asset",
                "quantity": {"standardQuantity": "1", "serialNumbers": ["SN-1"]},
            }
        ],
    }

    serials = service.get_asset_tag_serials(order_payload)

    assert len(serials) == 1
    assert serials[0]["serials"] == ["SN-2"]
    print("[PASS] Asset-tag serials only include unshipped remaining devices")


def test_partial_order_details_regenerate_instead_of_reusing_sharepoint_pdf():
    """Second-leg order details should be regenerated from remaining items."""

    class FakeSharePointService:
        is_enabled = True

        def __init__(self) -> None:
            self.download_called = False

        def download_file(self, subfolder: str, filename: str) -> bytes | None:
            self.download_called = True
            return b"stale-pdf"

        def upload_file(self, content: bytes, subfolder: str, filename: str) -> str:
            return f"sharepoint://{subfolder}/{filename}"

    fake_sp_service = FakeSharePointService()
    generated_payloads: list[dict[str, Any]] = []

    order = SimpleNamespace(
        id="order-4",
        inflow_order_id="TH1004",
        recipient_contact="user@example.com",
        recipient_name="User",
        inflow_data={
            "orderNumber": "TH1004",
            "lines": [
                {
                    "productId": "prod-asset",
                    "description": "Laptop",
                    "unitPrice": 1200,
                    "quantity": {
                        "standardQuantity": "2",
                        "serialNumbers": ["SN-1", "SN-2"],
                    },
                }
            ],
            "pickLines": [
                {
                    "productId": "prod-asset",
                    "description": "Laptop",
                    "unitPrice": 1200,
                    "quantity": {
                        "standardQuantity": "2",
                        "serialNumbers": ["SN-1", "SN-2"],
                    },
                }
            ],
            "packLines": [
                {
                    "productId": "prod-asset",
                    "quantity": {"standardQuantity": "1", "serialNumbers": ["SN-1"]},
                }
            ],
        },
        order_details_path=None,
        order_details_generated_at=None,
    )

    db = SimpleNamespace(commit=lambda: None, refresh=lambda _order: None)
    from app.services.order_service import OrderService

    service = OrderService(db=cast(Any, db))

    with tempfile.TemporaryDirectory() as tmpdir:
        service._local_doc_path = lambda category, filename: Path(tmpdir) / category / filename  # type: ignore[method-assign]

        with patch(
            "app.services.sharepoint_service.get_sharepoint_service",
            return_value=fake_sp_service,
        ):
            fake_pdf_module = types.ModuleType("app.services.pdf_service")
            fake_pdf_module.pdf_service = SimpleNamespace(
                generate_order_details_pdf=lambda payload: (
                    generated_payloads.append(payload) or b"fresh-pdf"
                )
            )
            with patch.dict(
                sys.modules,
                {"app.services.pdf_service": fake_pdf_module},
            ):
                with patch(
                    "app.services.email_service.email_service.is_configured",
                    return_value=True,
                ):
                    with patch(
                        "app.services.email_service.EmailService.is_enabled",
                        new_callable=PropertyMock,
                        return_value=False,
                    ):
                        success = service._send_order_details_email(order)

    assert success is False
    assert fake_sp_service.download_called is False
    assert len(generated_payloads) == 1
    assert generated_payloads[0]["lines"][0]["quantity"]["standardQuantity"] == "1"
    assert generated_payloads[0]["pickLines"][0]["quantity"]["serialNumbers"] == [
        "SN-2"
    ]
    assert order.order_details_path == "sharepoint://order-details/TH1004.pdf"
    assert order.order_details_generated_at is not None
    assert order.order_details_email_status == OrderService.ORDER_DETAILS_EMAIL_NOT_SENT
    assert order.order_details_email_status_updated_at is not None
    print("[PASS] Partial order details regenerate from remaining items")


def test_partial_order_details_raises_when_sharepoint_fails():
    """Order details generation should fail if required SharePoint upload is unavailable."""

    class FailingSharePointService:
        is_enabled = True

        def upload_file(self, content: bytes, subfolder: str, filename: str) -> str:
            raise RuntimeError("sharepoint unavailable")

    fake_sp_service = FailingSharePointService()
    generated_payloads: list[dict[str, Any]] = []

    order = SimpleNamespace(
        id="order-4b",
        inflow_order_id="TH1004-P",
        recipient_contact="user@example.com",
        recipient_name="User",
        inflow_sales_order_id="sales-order-1004",
        inflow_data={
            "orderNumber": "TH1004-P",
            "lines": [],
            "pickLines": [],
            "packLines": [],
            "orderRemarks": "",
        },
        order_details_path=None,
        order_details_generated_at=None,
    )

    db = SimpleNamespace(commit=lambda: None, refresh=lambda _order: None)
    from app.services.order_service import OrderService

    service = OrderService(db=cast(Any, db))

    with tempfile.TemporaryDirectory() as tmpdir:
        service._local_doc_path = lambda category, filename: Path(tmpdir) / category / filename  # type: ignore[method-assign]

        with patch(
            "app.services.sharepoint_service.get_sharepoint_service",
            return_value=fake_sp_service,
        ):
            fake_pdf_module = types.ModuleType("app.services.pdf_service")
            fake_pdf_module.pdf_service = SimpleNamespace(
                generate_order_details_pdf=lambda payload: (
                    generated_payloads.append(payload) or b"fresh-pdf"
                )
            )
            with patch.dict(
                sys.modules,
                {"app.services.pdf_service": fake_pdf_module},
            ):
                with patch(
                    "app.services.email_service.email_service.is_configured",
                    return_value=False,
                ):
                    try:
                        service._send_order_details_email(order)
                        raised = False
                    except RuntimeError as exc:
                        raised = True
                        assert "sharepoint unavailable" in str(exc)

        local_order_details_path = Path(tmpdir) / "orders" / "TH1004-P.pdf"
        assert local_order_details_path.exists()

    assert raised is True
    assert len(generated_payloads) == 1
    assert order.order_details_path is None
    assert order.order_details_generated_at is None
    print("[PASS] Partial order details fail when SharePoint upload fails")


def _make_sqlite_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return session_factory(), engine


def test_partial_picklist_leg_creation_links_parent_and_child():
    """Partial picklist splits should persist parent/child linkage without FK issues."""

    session, engine = _make_sqlite_session()
    session.execute(text("PRAGMA foreign_keys=ON"))

    original_order = Order(
        id="order-parent-2",
        inflow_order_id="TH2002",
        inflow_sales_order_id="sales-order-2002",
        recipient_name="User Two",
        recipient_contact="user.two@example.com",
        delivery_location="Building 202",
        po_number="PO-2002",
        status=OrderStatus.PICKED.value,
        inflow_data={
            "orderNumber": "TH2002",
            "lines": [
                {
                    "productId": "prod-1",
                    "description": "Laptop",
                    "quantity": {"standardQuantity": "2"},
                },
                {
                    "productId": "prod-2",
                    "description": "Dock",
                    "quantity": {"standardQuantity": "1"},
                },
            ],
            "pickLines": [
                {
                    "productId": "prod-1",
                    "description": "Laptop",
                    "quantity": {"standardQuantity": "1"},
                }
            ],
            "packLines": [
                {
                    "productId": "prod-1",
                    "quantity": {"standardQuantity": "1"},
                }
            ],
            "shipLines": [
                {
                    "containers": ["DELIVERY-TH2002-1"],
                }
            ],
        },
    )
    session.add(original_order)
    session.commit()

    try:
        service = OrderSplittingService(session)

        child_order = service.create_partial_picklist_leg(original_order, user_id="tech-2")

        assert child_order is not None
        assert child_order.inflow_order_id == "TH2002-P"
        assert child_order.parent_order_id == original_order.id
        assert original_order.has_remainder == "Y"
        assert original_order.remainder_order_id == child_order.id
        assert original_order.inflow_data["lines"] == [
            {
                "productId": "prod-1",
                "description": "Laptop",
                "quantity": {"standardQuantity": 1.0},
            },
            {
                "productId": "prod-2",
                "description": "Dock",
                "quantity": {"standardQuantity": 1.0},
            },
        ]
        assert original_order.inflow_data.get("pickLines") == []
        assert original_order.inflow_data.get("packLines") == []
        assert original_order.inflow_data.get("shipLines") == []
        assert child_order.inflow_data["lines"] == [
            {
                "productId": "prod-1",
                "description": "Laptop",
                "quantity": {"standardQuantity": "1.0"},
            }
        ]
        assert child_order.inflow_data["pickLines"] == [
            {
                "productId": "prod-1",
                "description": "Laptop",
                "quantity": {"standardQuantity": "1.0"},
            }
        ]
        assert child_order.inflow_data.get("packLines") == []
        assert child_order.inflow_data.get("shipLines") == []
        assert session.query(Order).filter(Order.inflow_order_id == "TH2002-P").count() == 1
    finally:
        session.close()
        engine.dispose()


def test_create_order_from_inflow_preserves_existing_split_payload():
    """Webhook refreshes should preserve split lines but allow fresh pick state through."""

    session, engine = _make_sqlite_session()

    existing_order = Order(
        id="order-parent-sync-1",
        inflow_order_id="TH3006",
        inflow_sales_order_id="sales-order-3006",
        recipient_name="Old Recipient",
        recipient_contact="old@example.com",
        delivery_location="Old Building",
        po_number="PO-3006",
        status=OrderStatus.PRE_DELIVERY.value,
        has_remainder="Y",
        remainder_order_id="child-order-sync-1",
        inflow_data={
            "orderNumber": "TH3006",
            "salesOrderId": "sales-order-3006",
            "contactName": "Old Recipient",
            "email": "old@example.com",
            "shippingAddress": {"address1": "Old Building"},
            "lines": [
                {
                    "productId": "prod-a",
                    "description": "Hub Adapter",
                    "quantity": {"standardQuantity": "3"},
                    "unitPrice": 47,
                }
            ],
            "pickLines": [
                {
                    "productId": "prod-a",
                    "description": "Hub Adapter",
                    "quantity": {"standardQuantity": "3"},
                    "unitPrice": 47,
                }
            ],
            "packLines": [],
            "shipLines": [],
        },
    )
    session.add(existing_order)
    session.commit()

    service = OrderService(session)
    incoming_payload = {
        "orderNumber": "TH3006",
        "salesOrderId": "sales-order-3006",
        "contactName": "Updated Recipient",
        "email": "updated@example.com",
        "shippingAddress": {"address1": "Updated Building"},
        "poNumber": "PO-3006-NEW",
        "lines": [
            {
                "productId": "prod-a",
                "description": "Hub Adapter",
                "quantity": {"standardQuantity": "3"},
                "unitPrice": 47,
            },
            {
                "productId": "prod-b",
                "description": "Surge Protector",
                "quantity": {"standardQuantity": "2"},
                "unitPrice": 15,
            },
        ],
        "pickLines": [
            {
                "productId": "prod-a",
                "description": "Hub Adapter",
                "quantity": {"standardQuantity": "3"},
                "unitPrice": 47,
            },
            {
                "productId": "prod-b",
                "description": "Surge Protector",
                "quantity": {"standardQuantity": "2"},
                "unitPrice": 15,
            },
        ],
        "packLines": [
            {
                "productId": "prod-a",
                "quantity": {"standardQuantity": "3"},
                "containerNumber": "DELIVERY-TH3006-1",
            }
        ],
        "shipLines": [
            {
                "carrier": "TechHub",
                "containers": ["DELIVERY-TH3006-1"],
            }
        ],
    }

    updated = service.create_order_from_inflow(incoming_payload)
    session.refresh(updated)

    assert updated.recipient_name == "Updated Recipient"
    assert updated.recipient_contact == "updated@example.com"
    assert updated.delivery_location == "Updated Building"
    assert updated.po_number == "PO-3006-NEW"
    assert updated.inflow_data["lines"] == [
        {
            "productId": "prod-a",
            "description": "Hub Adapter",
            "quantity": {"standardQuantity": "3"},
            "unitPrice": 47,
        }
    ]
    assert updated.inflow_data["pickLines"] == [
        {
            "productId": "prod-a",
            "description": "Hub Adapter",
            "quantity": {"standardQuantity": "3"},
            "unitPrice": 47,
        },
        {
            "productId": "prod-b",
            "description": "Surge Protector",
            "quantity": {"standardQuantity": "2"},
            "unitPrice": 15,
        }
    ]
    assert updated.inflow_data["packLines"] == [
        {
            "productId": "prod-a",
            "quantity": {"standardQuantity": "3"},
            "containerNumber": "DELIVERY-TH3006-1",
        }
    ]
    assert updated.inflow_data["shipLines"] == [
        {
            "carrier": "TechHub",
            "containers": ["DELIVERY-TH3006-1"],
        }
    ]


def test_create_order_from_inflow_refreshes_pick_lines_for_split_orders():
    """Split remainder legs should accept refreshed pickLines so they can unblock."""

    session, engine = _make_sqlite_session()

    existing_order = Order(
        id="order-parent-sync-2",
        inflow_order_id="TH3007",
        inflow_sales_order_id="sales-order-3007",
        recipient_name="Old Recipient",
        recipient_contact="old@example.com",
        delivery_location="Old Building",
        po_number="PO-3007",
        status=OrderStatus.PRE_DELIVERY.value,
        has_remainder="Y",
        remainder_order_id="child-order-sync-2",
        inflow_data={
            "orderNumber": "TH3007",
            "salesOrderId": "sales-order-3007",
            "contactName": "Old Recipient",
            "email": "old@example.com",
            "shippingAddress": {"address1": "Old Building"},
            "lines": [
                {
                    "productId": "prod-b",
                    "description": "Surge Protector",
                    "quantity": {"standardQuantity": "2"},
                    "unitPrice": 15,
                }
            ],
            "pickLines": [],
            "packLines": [],
            "shipLines": [],
        },
    )
    session.add(existing_order)
    session.commit()

    service = OrderService(session)
    incoming_payload = {
        "orderNumber": "TH3007",
        "salesOrderId": "sales-order-3007",
        "contactName": "Updated Recipient",
        "email": "updated@example.com",
        "shippingAddress": {"address1": "Updated Building"},
        "poNumber": "PO-3007-NEW",
        "lines": [
            {
                "productId": "prod-a",
                "description": "Hub Adapter",
                "quantity": {"standardQuantity": "3"},
                "unitPrice": 47,
            },
            {
                "productId": "prod-b",
                "description": "Surge Protector",
                "quantity": {"standardQuantity": "2"},
                "unitPrice": 15,
            },
        ],
        "pickLines": [
            {
                "productId": "prod-b",
                "description": "Surge Protector",
                "quantity": {"standardQuantity": "2"},
                "unitPrice": 15,
            }
        ],
        "packLines": [
            {
                "productId": "prod-b",
                "quantity": {"standardQuantity": "2"},
                "containerNumber": "DELIVERY-TH3007-1",
            }
        ],
        "shipLines": [
            {
                "carrier": "TechHub",
                "containers": ["DELIVERY-TH3007-1"],
            }
        ],
    }

    updated = service.create_order_from_inflow(incoming_payload)
    session.refresh(updated)

    assert updated.inflow_data["lines"] == [
        {
            "productId": "prod-b",
            "description": "Surge Protector",
            "quantity": {"standardQuantity": "2"},
            "unitPrice": 15,
        }
    ]
    assert updated.inflow_data["pickLines"] == [
        {
            "productId": "prod-b",
            "description": "Surge Protector",
            "quantity": {"standardQuantity": "2"},
            "unitPrice": 15,
        }
    ]
    assert updated.inflow_data["packLines"] == [
        {
            "productId": "prod-b",
            "quantity": {"standardQuantity": "2"},
            "containerNumber": "DELIVERY-TH3007-1",
        }
    ]
    assert updated.inflow_data["shipLines"] == [
        {
            "carrier": "TechHub",
            "containers": ["DELIVERY-TH3007-1"],
        }
    ]

    session.close()
    engine.dispose()


def test_create_order_from_inflow_prefers_exact_order_number_for_split_orders():
    """A split parent should be refreshed by its exact order number, not the shared sales order id."""

    session, engine = _make_sqlite_session()

    parent_order = Order(
        id="order-parent-sync-3",
        inflow_order_id="TH3008",
        inflow_sales_order_id="sales-order-3008",
        recipient_name="Parent Recipient",
        recipient_contact="parent@example.com",
        delivery_location="Parent Building",
        po_number="PO-3008",
        status=OrderStatus.PRE_DELIVERY.value,
        has_remainder="Y",
        remainder_order_id="child-order-sync-3",
        inflow_data={
            "orderNumber": "TH3008",
            "salesOrderId": "sales-order-3008",
            "contactName": "Parent Recipient",
            "email": "parent@example.com",
            "shippingAddress": {"address1": "Parent Building"},
            "lines": [
                {
                    "productId": "prod-b",
                    "description": "Surge Protector",
                    "quantity": {"standardQuantity": "2"},
                    "unitPrice": 15,
                }
            ],
            "pickLines": [],
            "packLines": [],
            "shipLines": [],
        },
    )
    child_order = Order(
        id="order-child-sync-3",
        inflow_order_id="TH3008-P",
        inflow_sales_order_id="sales-order-3008",
        recipient_name="Picked Recipient",
        recipient_contact="picked@example.com",
        delivery_location="Parent Building",
        po_number="PO-3008",
        status=OrderStatus.PICKED.value,
        parent_order_id=parent_order.id,
        inflow_data={
            "orderNumber": "TH3008-P",
            "salesOrderId": "sales-order-3008",
            "contactName": "Picked Recipient",
            "email": "picked@example.com",
            "shippingAddress": {"address1": "Parent Building"},
            "lines": [
                {
                    "productId": "prod-a",
                    "description": "Hub Adapter",
                    "quantity": {"standardQuantity": "1"},
                    "unitPrice": 47,
                }
            ],
            "pickLines": [
                {
                    "productId": "prod-a",
                    "description": "Hub Adapter",
                    "quantity": {"standardQuantity": "1"},
                    "unitPrice": 47,
                }
            ],
            "packLines": [],
            "shipLines": [],
        },
    )
    session.add(parent_order)
    session.commit()
    session.add(child_order)
    session.commit()

    service = OrderService(session)
    incoming_payload = {
        "orderNumber": "TH3008",
        "salesOrderId": "sales-order-3008",
        "contactName": "Parent Recipient Updated",
        "email": "parent-updated@example.com",
        "shippingAddress": {"address1": "Updated Parent Building"},
        "poNumber": "PO-3008-NEW",
        "lines": [
            {
                "productId": "prod-b",
                "description": "Surge Protector",
                "quantity": {"standardQuantity": "2"},
                "unitPrice": 15,
            }
        ],
        "pickLines": [
            {
                "productId": "prod-b",
                "description": "Surge Protector",
                "quantity": {"standardQuantity": "2"},
                "unitPrice": 15,
            }
        ],
        "packLines": [],
        "shipLines": [],
    }

    updated = service.create_order_from_inflow(incoming_payload)
    session.refresh(parent_order)
    session.refresh(child_order)

    assert updated.id == parent_order.id
    assert parent_order.recipient_name == "Parent Recipient Updated"
    assert parent_order.inflow_data["pickLines"] == [
        {
            "productId": "prod-b",
            "description": "Surge Protector",
            "quantity": {"standardQuantity": "2"},
            "unitPrice": 15,
        }
    ]
    assert child_order.recipient_name == "Picked Recipient"
    assert child_order.inflow_data["pickLines"] == [
        {
            "productId": "prod-a",
            "description": "Hub Adapter",
            "quantity": {"standardQuantity": "1"},
            "unitPrice": 47,
        }
    ]

    session.close()
    engine.dispose()


def test_partial_order_remainder_creation_links_parent_and_child():
    """Partial picks should create a linked remainder order with only missing items."""
def test_generate_picklist_keeps_parent_active_when_partial_leg_already_exists():
    """Generating a partial picklist should not switch the active order to the child leg."""

    session, engine = _make_sqlite_session()
    session.execute(text("PRAGMA foreign_keys=ON"))

    parent_order = Order(
        id="order-parent-3",
        inflow_order_id="TH3001",
        inflow_sales_order_id="sales-order-3001",
        recipient_name="User Three",
        recipient_contact="user.three@example.com",
        delivery_location="Building 303",
        po_number="PO-3001",
        status=OrderStatus.PICKED.value,
        tagged_by="tech@example.com",
        inflow_data={
            "orderNumber": "TH3001",
            "contactName": "User Three",
            "email": "user.three@example.com",
            "shippingAddress": {"address1": "303 Example St"},
            "lines": [
                {
                    "productId": "prod-1",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "1"},
                },
                {
                    "productId": "prod-2",
                    "product": {"name": "Dock", "sku": "DOCK-1"},
                    "quantity": {"standardQuantity": "1"},
                },
            ],
            "pickLines": [],
        },
    )
    partial_leg = Order(
        id="order-child-3",
        inflow_order_id="TH3001-P",
        inflow_sales_order_id="sales-order-3001",
        recipient_name="User Three",
        recipient_contact="user.three@example.com",
        delivery_location="Building 303",
        po_number="PO-3001",
        status=OrderStatus.PICKED.value,
        parent_order_id=parent_order.id,
        inflow_data={
            "orderNumber": "TH3001-P",
            "contactName": "User Three",
            "email": "user.three@example.com",
            "shippingAddress": {"address1": "303 Example St"},
            "lines": [
                {
                    "productId": "prod-1",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "1"},
                }
            ],
            "pickLines": [
                {
                    "productId": "prod-1",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "1"},
                }
            ],
        },
    )
    parent_order.inflow_data = {
        "orderNumber": "TH3001",
        "contactName": "User Three",
        "email": "user.three@example.com",
        "shippingAddress": {"address1": "303 Example St"},
        "lines": [
            {
                "productId": "prod-2",
                "product": {"name": "Dock", "sku": "DOCK-1"},
                "quantity": {"standardQuantity": "1"},
            }
        ],
        "pickLines": [
            {
                "productId": "prod-2",
                "product": {"name": "Dock", "sku": "DOCK-1"},
                "quantity": {"standardQuantity": "1"},
            }
        ],
    }
    session.add(parent_order)
    session.commit()

    session.add(partial_leg)
    session.commit()

    parent_order.has_remainder = "Y"
    parent_order.remainder_order_id = partial_leg.id
    session.commit()

    from app.services.order_service import OrderService

    service = OrderService(session)

    class FakeSharePointService:
        is_enabled = True

        def upload_pdf(self, pdf_path: str, subfolder: str, filename: str) -> str:
            return f"sharepoint://{subfolder}/{filename}"

    def fake_generate_picklist_pdf(self, inflow_data, output_path):
        Path(output_path).write_bytes(b"%PDF-1.4 fake picklist\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        service._local_doc_path = lambda category, filename: Path(tmpdir) / category / filename  # type: ignore[method-assign]

        with patch("app.services.sharepoint_service.get_sharepoint_service", return_value=FakeSharePointService()):
            with patch(
                "app.services.picklist_service.PicklistService.generate_picklist_pdf",
                new=fake_generate_picklist_pdf,
            ):
                with patch(
                    "app.services.order_service.SystemSettingService.is_setting_enabled",
                    return_value=False,
                ):
                    with patch(
                        "app.services.order_service.SystemSettingService.get_setting",
                        return_value="false",
                    ):
                        with patch.object(service, "_send_order_details_email", return_value=True):
                            result = service.generate_picklist(
                                parent_order.id,
                                generated_by="tech@example.com",
                                generated_by_display="tech@example.com",
                                create_partial_leg=True,
                            )

    assert result.id == parent_order.id
    assert result.inflow_order_id == "TH3001"
    assert parent_order.picklist_path is not None
    assert partial_leg.picklist_path is None

    session.close()
    engine.dispose()


def test_generate_picklist_uses_parent_remainder_items_when_child_leg_exists():
    """Parent remainder docs should print only the remaining lines, not the original sales order."""

    session, engine = _make_sqlite_session()
    parent_order = Order(
        id="order-parent-4",
        inflow_order_id="TH3002",
        inflow_sales_order_id="sales-order-3002",
        recipient_name="User Four",
        recipient_contact="user.four@example.com",
        delivery_location="Building 404",
        po_number="PO-3002",
        status=OrderStatus.PICKED.value,
        tagged_by="tech@example.com",
        inflow_data={
            "orderNumber": "TH3002",
            "contactName": "User Four",
            "email": "user.four@example.com",
            "shippingAddress": {"address1": "404 Example St"},
            "lines": [
                {
                    "productId": "prod-2",
                    "product": {"name": "Dock", "sku": "DOCK-1"},
                    "quantity": {"standardQuantity": "1"},
                },
            ],
            "pickLines": [
                {
                    "productId": "prod-2",
                    "product": {"name": "Dock", "sku": "DOCK-1"},
                    "quantity": {"standardQuantity": "1"},
                }
            ],
        },
    )
    child_order = Order(
        id="order-child-4",
        inflow_order_id="TH3002-P",
        inflow_sales_order_id="sales-order-3002",
        recipient_name="User Four",
        recipient_contact="user.four@example.com",
        delivery_location="Building 404",
        po_number="PO-3002",
        status=OrderStatus.PICKED.value,
        parent_order_id=parent_order.id,
        inflow_data={
            "orderNumber": "TH3002-P",
            "contactName": "User Four",
            "email": "user.four@example.com",
            "shippingAddress": {"address1": "404 Example St"},
            "lines": [
                {
                    "productId": "prod-1",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "1"},
                }
            ],
            "pickLines": [
                {
                    "productId": "prod-1",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "1"},
                }
            ],
        },
    )
    session.add(parent_order)
    session.commit()
    session.add(child_order)
    session.commit()
    parent_order.has_remainder = "Y"
    parent_order.remainder_order_id = child_order.id
    session.commit()

    from app.services.order_service import OrderService
    from app.services.order_splitting import OrderSplittingService

    service = OrderService(session)
    picklist_view = OrderSplittingService(session).build_parent_remainder_picklist_view(parent_order)

    assert picklist_view is not None
    assert picklist_view["lines"] == [
        {
            "productId": "prod-2",
            "product": {"name": "Dock", "sku": "DOCK-1"},
            "quantity": {"standardQuantity": 1.0},
        },
    ]
    assert picklist_view["pickLines"] == picklist_view["lines"]

    class FakeSharePointService:
        is_enabled = True

        def upload_pdf(self, pdf_path: str, subfolder: str, filename: str) -> str:
            return f"sharepoint://{subfolder}/{filename}"

    def fake_generate_picklist_pdf(self, inflow_data, output_path):
        Path(output_path).write_bytes(b"%PDF-1.4 fake picklist\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        service._local_doc_path = lambda category, filename: Path(tmpdir) / category / filename  # type: ignore[method-assign]

        with patch("app.services.sharepoint_service.get_sharepoint_service", return_value=FakeSharePointService()):
            with patch(
                "app.services.picklist_service.PicklistService.generate_picklist_pdf",
                new=fake_generate_picklist_pdf,
            ):
                with patch(
                    "app.services.order_service.SystemSettingService.is_setting_enabled",
                    return_value=False,
                ), patch(
                    "app.services.order_service.SystemSettingService.get_setting",
                    return_value="false",
                ):
                    with patch.object(service, "_send_order_details_email", return_value=True):
                        result = service.generate_picklist(
                            parent_order.id,
                            generated_by="tech@example.com",
                            generated_by_display="tech@example.com",
                            create_partial_leg=False,
                        )

    assert result.id == parent_order.id
    assert result.inflow_order_id == "TH3002"
    assert parent_order.picklist_path is not None
    assert child_order.picklist_path is None

    session.close()
    engine.dispose()


def test_recursive_remainder_split_creates_next_partial_leg():
    """A partially picked remainder row should create a new recursive picked leg."""

    session, engine = _make_sqlite_session()

    parent_order = Order(
        id="order-parent-8",
        inflow_order_id="TH3009",
        inflow_sales_order_id="sales-order-3009",
        recipient_name="User Eight",
        recipient_contact="user.eight@example.com",
        delivery_location="Building 808",
        po_number="PO-3009",
        status=OrderStatus.PICKED.value,
        tagged_by="tech@example.com",
        inflow_data={
            "orderNumber": "TH3009",
            "contactName": "User Eight",
            "email": "user.eight@example.com",
            "shippingAddress": {"address1": "808 Example St"},
            "lines": [
                {
                    "productId": "prod-a",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "2"},
                }
            ],
            "pickLines": [
                {
                    "productId": "prod-a",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "1"},
                }
            ],
        },
    )
    first_child = Order(
        id="order-child-8",
        inflow_order_id="TH3009-P",
        inflow_sales_order_id="sales-order-3009",
        recipient_name="User Eight",
        recipient_contact="user.eight@example.com",
        delivery_location="Building 808",
        po_number="PO-3009",
        status=OrderStatus.PICKED.value,
        parent_order_id=parent_order.id,
        inflow_data={
            "orderNumber": "TH3009-P",
            "contactName": "User Eight",
            "email": "user.eight@example.com",
            "shippingAddress": {"address1": "808 Example St"},
            "lines": [
                {
                    "productId": "prod-a",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "1"},
                }
            ],
            "pickLines": [
                {
                    "productId": "prod-a",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "1"},
                }
            ],
        },
    )

    session.add(parent_order)
    session.commit()
    session.add(first_child)
    session.commit()

    parent_order.has_remainder = "Y"
    parent_order.remainder_order_id = first_child.id
    parent_order.inflow_data = {
        "orderNumber": "TH3009",
        "contactName": "User Eight",
        "email": "user.eight@example.com",
        "shippingAddress": {"address1": "808 Example St"},
        "lines": [
            {
                "productId": "prod-a",
                "product": {"name": "Laptop", "sku": "LAP-1"},
                "quantity": {"standardQuantity": "2"},
            }
        ],
        "pickLines": [
            {
                "productId": "prod-a",
                "product": {"name": "Laptop", "sku": "LAP-1"},
                "quantity": {"standardQuantity": "1"},
            }
        ],
    }
    session.commit()

    service = OrderService(session)

    assert service._parent_remainder_has_unpicked_items(parent_order) is True
    with pytest.raises(
        ValidationError, match="remainder leg still has items waiting to be picked"
    ):
        service.mark_asset_tagged(parent_order.id, ["TAG-1"], technician="tech@example.com")

    with pytest.raises(
        ValidationError, match="remainder leg still has items waiting to be picked"
    ):
        service.send_order_details_email(parent_order.id, generated_by="tech@example.com")

    class FakeSharePointService:
        is_enabled = True

        def upload_pdf(self, pdf_path: str, subfolder: str, filename: str) -> str:
            return f"sharepoint://{subfolder}/{filename}"

    def fake_generate_picklist_pdf(self, inflow_data, output_path):
        Path(output_path).write_bytes(b"%PDF-1.4 fake recursive picklist\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        service._local_doc_path = lambda category, filename: Path(tmpdir) / category / filename  # type: ignore[method-assign]

        with patch("app.services.sharepoint_service.get_sharepoint_service", return_value=FakeSharePointService()):
            with patch(
                "app.services.picklist_service.PicklistService.generate_picklist_pdf",
                new=fake_generate_picklist_pdf,
            ):
                with patch(
                    "app.services.order_service.SystemSettingService.is_setting_enabled",
                    return_value=False,
                ), patch(
                    "app.services.order_service.SystemSettingService.get_setting",
                    return_value="false",
                ):
                    with patch.object(service, "_send_order_details_email", return_value=True):
                        result = service.generate_picklist(
                            parent_order.id,
                            generated_by="tech@example.com",
                            generated_by_display="tech@example.com",
                            create_partial_leg=False,
                        )

    session.refresh(parent_order)
    session.refresh(first_child)
    session.refresh(result)

    assert result.id != parent_order.id
    assert result.inflow_order_id == "TH3009-P2"
    assert result.parent_order_id == parent_order.id
    assert parent_order.remainder_order_id == result.id
    assert parent_order.inflow_data["lines"] == [
        {
            "productId": "prod-a",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": 1.0},
        }
    ]
    assert parent_order.inflow_data["pickLines"] == []
    assert first_child.inflow_data["lines"] == [
        {
            "productId": "prod-a",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": "1"},
        }
    ]
    assert result.inflow_data["lines"] == [
        {
            "productId": "prod-a",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": "1.0"},
        }
    ]
    assert result.inflow_data["pickLines"] == [
        {
            "productId": "prod-a",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": "1.0"},
        }
    ]

    session.close()
    engine.dispose()


def test_recursive_partial_split_uses_delta_picks_for_next_child_leg():
    """Later recursive legs should only capture the newest picked delta, not prior child quantities."""

    session, engine = _make_sqlite_session()

    parent_order = Order(
        id="order-parent-8b",
        inflow_order_id="TH4770",
        inflow_sales_order_id="sales-order-4770",
        recipient_name="User Eight",
        recipient_contact="user.eight@example.com",
        delivery_location="Building 4770",
        po_number="PO-4770",
        status=OrderStatus.PICKED.value,
        tagged_by="tech@example.com",
        inflow_data={
            "orderNumber": "TH4770",
            "contactName": "User Eight",
            "email": "user.eight@example.com",
            "shippingAddress": {"address1": "4770 Example St"},
            "lines": [
                {
                    "productId": "prod-laptop",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "85"},
                }
            ],
            "pickLines": [
                {
                    "productId": "prod-laptop",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "20"},
                }
            ],
        },
    )
    session.add(parent_order)
    session.commit()

    splitting_service = OrderSplittingService(session)
    first_child = splitting_service.create_partial_picklist_leg(parent_order, user_id="tech@example.com")
    session.refresh(parent_order)

    assert first_child is not None
    session.refresh(first_child)
    assert first_child.inflow_order_id == "TH4770-P"
    assert first_child.inflow_data["lines"] == [
        {
            "productId": "prod-laptop",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": "20.0"},
        }
    ]
    assert parent_order.inflow_data["lines"] == [
        {
            "productId": "prod-laptop",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": 65.0},
        }
    ]

    order_service = OrderService(session)
    cumulative_refresh = {
        "orderNumber": "TH4770",
        "salesOrderId": "sales-order-4770",
        "contactName": "User Eight",
        "email": "user.eight@example.com",
        "shippingAddress": {"address1": "4770 Example St"},
        "lines": [
            {
                "productId": "prod-laptop",
                "product": {"name": "Laptop", "sku": "LAP-1"},
                "quantity": {"standardQuantity": "65"},
            }
        ],
        "pickLines": [
            {
                "productId": "prod-laptop",
                "product": {"name": "Laptop", "sku": "LAP-1"},
                "quantity": {"standardQuantity": "60"},
            }
        ],
        "packLines": [],
        "shipLines": [],
    }

    updated = order_service.create_order_from_inflow(cumulative_refresh)
    session.refresh(updated)

    assert updated.inflow_data["pickLines"] == [
        {
            "productId": "prod-laptop",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": 40.0},
        }
    ]

    class FakeSharePointService:
        is_enabled = True

        def upload_pdf(self, pdf_path: str, subfolder: str, filename: str) -> str:
            return f"sharepoint://{subfolder}/{filename}"

    def fake_generate_picklist_pdf(self, inflow_data, output_path):
        Path(output_path).write_bytes(b"%PDF-1.4 fake recursive picklist\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        order_service._local_doc_path = lambda category, filename: Path(tmpdir) / category / filename  # type: ignore[method-assign]

        with patch("app.services.sharepoint_service.get_sharepoint_service", return_value=FakeSharePointService()):
            with patch(
                "app.services.picklist_service.PicklistService.generate_picklist_pdf",
                new=fake_generate_picklist_pdf,
            ):
                with patch(
                    "app.services.order_service.SystemSettingService.is_setting_enabled",
                    return_value=False,
                ), patch(
                    "app.services.order_service.SystemSettingService.get_setting",
                    return_value="false",
                ):
                    with patch.object(order_service, "_send_order_details_email", return_value=True):
                        result = order_service.generate_picklist(
                            parent_order.id,
                            generated_by="tech@example.com",
                            generated_by_display="tech@example.com",
                            create_partial_leg=False,
                        )

    session.refresh(parent_order)
    session.refresh(first_child)
    session.refresh(result)

    assert result.inflow_order_id == "TH4770-P2"
    assert result.parent_order_id == parent_order.id
    assert result.inflow_data["lines"] == [
        {
            "productId": "prod-laptop",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": "40.0"},
        }
    ]
    assert result.inflow_data["pickLines"] == [
        {
            "productId": "prod-laptop",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": "40.0"},
        }
    ]
    assert parent_order.inflow_data["lines"] == [
        {
            "productId": "prod-laptop",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": 25.0},
        }
    ]
    assert parent_order.inflow_data["pickLines"] == []

    full_order_refresh = {
        "orderNumber": "TH4770",
        "salesOrderId": "sales-order-4770",
        "contactName": "User Eight",
        "email": "user.eight@example.com",
        "shippingAddress": {"address1": "4770 Example St"},
        "lines": [
            {
                "productId": "prod-laptop",
                "product": {"name": "Laptop", "sku": "LAP-1"},
                "quantity": {"standardQuantity": "85"},
            }
        ],
        "pickLines": [
            {
                "productId": "prod-laptop",
                "product": {"name": "Laptop", "sku": "LAP-1"},
                "quantity": {"standardQuantity": "60"},
            }
        ],
        "packLines": [],
        "shipLines": [],
    }

    refreshed = order_service.create_order_from_inflow(full_order_refresh)
    session.refresh(refreshed)

    assert refreshed.id == parent_order.id
    assert refreshed.inflow_data["lines"] == [
        {
            "productId": "prod-laptop",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": 25.0},
        }
    ]
    assert refreshed.inflow_data["pickLines"] == []

    stale_full_snapshot = {
        **full_order_refresh,
        "lines": [
            {
                "productId": "prod-laptop",
                "product": {"name": "Laptop", "sku": "LAP-1"},
                "quantity": {
                    "standardQuantity": "85",
                    "serialNumbers": ["FIRST-LEG", "SECOND-LEG"],
                },
            }
        ],
        "pickLines": [
            {
                "productId": "prod-laptop",
                "product": {"name": "Laptop", "sku": "LAP-1"},
                "quantity": {
                    "standardQuantity": "60",
                    "serialNumbers": ["FIRST-LEG", "SECOND-LEG"],
                },
            }
        ],
    }
    parent_order.inflow_data = stale_full_snapshot
    session.commit()

    repaired_document_view = splitting_service.build_parent_remainder_document_view(
        parent_order
    )

    assert repaired_document_view is not None
    assert repaired_document_view["lines"] == [
        {
            "productId": "prod-laptop",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": 25.0, "serialNumbers": []},
        }
    ]
    assert repaired_document_view["pickLines"] == []

    session.close()
    engine.dispose()


def test_recursive_partial_split_requires_base_order_number():
    """Recursive child ID generation should fail fast when the base order number is blank."""

    session, engine = _make_sqlite_session()

    parent_order = Order(
        id="order-parent-blank",
        inflow_order_id="   ",
        inflow_sales_order_id="sales-order-blank",
        recipient_name="User Blank",
        recipient_contact="user.blank@example.com",
        delivery_location="Building 909",
        po_number="PO-blank",
        status=OrderStatus.PICKED.value,
        inflow_data={
            "orderNumber": "   ",
            "lines": [
                {
                    "productId": "prod-a",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "2"},
                }
            ],
            "pickLines": [
                {
                    "productId": "prod-a",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "1"},
                }
            ],
        },
    )
    session.add(parent_order)
    session.commit()

    service = OrderSplittingService(session)

    with pytest.raises(ValidationError, match="base inflow order number"):
        service.create_partial_picklist_leg(parent_order, user_id="tech@example.com")

    session.close()
    engine.dispose()


def test_parent_remainder_document_view_keeps_items_when_fully_picked():
    """A remainder leg should keep showing its leg items even after all of them are picked."""

    session, engine = _make_sqlite_session()
    parent_order = Order(
        id="order-parent-5",
        inflow_order_id="TH3003",
        inflow_sales_order_id="sales-order-3003",
        recipient_name="User Five",
        recipient_contact="user.five@example.com",
        delivery_location="Building 505",
        po_number="PO-3003",
        status=OrderStatus.PICKED.value,
        inflow_data={
            "orderNumber": "TH3003",
            "contactName": "User Five",
            "email": "user.five@example.com",
            "shippingAddress": {"address1": "505 Example St"},
            "lines": [
                {
                    "productId": "prod-1",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "1"},
                },
                {
                    "productId": "prod-2",
                    "product": {"name": "Dock", "sku": "DOCK-1"},
                    "quantity": {"standardQuantity": "1"},
                },
            ],
            "pickLines": [
                {
                    "productId": "prod-1",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "1"},
                }
            ],
        },
    )
    session.add(parent_order)
    session.commit()

    from app.services.order_splitting import OrderSplittingService

    service = OrderSplittingService(session)
    service.create_partial_picklist_leg(parent_order, user_id="tech@example.com")
    session.refresh(parent_order)

    # Simulate the remainder leg being fully picked later in its own workflow.
    parent_order.inflow_data = {
        **parent_order.inflow_data,
        "pickLines": [
            {
                "productId": "prod-2",
                "product": {"name": "Dock", "sku": "DOCK-1"},
                "quantity": {"standardQuantity": "1"},
            },
        ],
    }
    session.commit()

    document_view = service.build_parent_remainder_document_view(parent_order)

    assert document_view is not None
    assert document_view["lines"] == [
        {
            "productId": "prod-2",
            "product": {"name": "Dock", "sku": "DOCK-1"},
            "quantity": {"standardQuantity": 1.0},
        },
    ]
    assert document_view["pickLines"] == [
        {
            "productId": "prod-2",
            "product": {"name": "Dock", "sku": "DOCK-1"},
            "quantity": {"standardQuantity": 1.0},
        },
    ]

    session.close()
    engine.dispose()


def test_parent_remainder_document_view_keeps_current_remainder_snapshot_for_same_product_split():
    """A remainder leg should keep its own picked items even when the split uses the same product IDs."""

    session, engine = _make_sqlite_session()
    parent_order = Order(
        id="order-parent-6",
        inflow_order_id="TH3004",
        inflow_sales_order_id="sales-order-3004",
        recipient_name="User Six",
        recipient_contact="user.six@example.com",
        delivery_location="Building 606",
        po_number="PO-3004",
        status=OrderStatus.PICKED.value,
        inflow_data={
            "orderNumber": "TH3004",
            "contactName": "User Six",
            "email": "user.six@example.com",
            "shippingAddress": {"address1": "606 Example St"},
            "lines": [
                {
                    "productId": "prod-1",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "1"},
                },
                {
                    "productId": "prod-2",
                    "product": {"name": "Dock", "sku": "DOCK-1"},
                    "quantity": {"standardQuantity": "1"},
                },
            ],
            "pickLines": [
                {
                    "productId": "prod-1",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "1"},
                }
            ],
        },
    )
    session.add(parent_order)
    session.commit()

    from app.services.order_splitting import OrderSplittingService

    service = OrderSplittingService(session)
    child_order = service.create_partial_picklist_leg(parent_order, user_id="tech@example.com")
    session.refresh(parent_order)

    assert child_order is not None

    # Simulate the remainder leg being refreshed from InFlow with the picked remainder items.
    parent_order.inflow_data = {
        "orderNumber": "TH3004",
        "contactName": "User Six",
        "email": "user.six@example.com",
        "shippingAddress": {"address1": "606 Example St"},
        "lines": [
            {
                "productId": "prod-1",
                "product": {"name": "Laptop", "sku": "LAP-1"},
                "quantity": {"standardQuantity": "1"},
            },
        ],
        "pickLines": [
            {
                "productId": "prod-1",
                "product": {"name": "Laptop", "sku": "LAP-1"},
                "quantity": {"standardQuantity": "1"},
            },
        ],
    }
    session.commit()

    document_view = service.build_parent_remainder_document_view(parent_order)
    pick_status_source = service.build_parent_remainder_pick_status_source(parent_order)

    assert document_view is not None
    assert document_view["lines"] == [
        {
            "productId": "prod-1",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": 1.0},
        },
    ]
    assert document_view["pickLines"] == [
        {
            "productId": "prod-1",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": 1.0},
        },
    ]
    assert pick_status_source is not None
    assert pick_status_source["lines"] == document_view["lines"]
    assert pick_status_source["pickLines"] == document_view["pickLines"]

    session.close()
    engine.dispose()


def test_parent_remainder_pick_status_source_excludes_child_leg_picks():
    """Remainder pick status should not count the picked-leg quantities after a split."""

    session, engine = _make_sqlite_session()
    parent_order = Order(
        id="order-parent-7",
        inflow_order_id="TH3005",
        inflow_sales_order_id="sales-order-3005",
        recipient_name="User Seven",
        recipient_contact="user.seven@example.com",
        delivery_location="Building 707",
        po_number="PO-3005",
        status=OrderStatus.PICKED.value,
        inflow_data={
            "orderNumber": "TH3005",
            "contactName": "User Seven",
            "email": "user.seven@example.com",
            "shippingAddress": {"address1": "707 Example St"},
            "lines": [
                {
                    "productId": "prod-1",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "2"},
                },
            ],
            "pickLines": [
                {
                    "productId": "prod-1",
                    "product": {"name": "Laptop", "sku": "LAP-1"},
                    "quantity": {"standardQuantity": "1"},
                }
            ],
        },
    )
    session.add(parent_order)
    session.commit()

    from app.services.order_splitting import OrderSplittingService

    service = OrderSplittingService(session)
    child_order = service.create_partial_picklist_leg(parent_order, user_id="tech@example.com")
    session.refresh(parent_order)

    assert child_order is not None

    # Simulate the parent payload drifting back to the original order data.
    parent_order.inflow_data = {
        "orderNumber": "TH3005",
        "contactName": "User Seven",
        "email": "user.seven@example.com",
        "shippingAddress": {"address1": "707 Example St"},
        "lines": [
            {
                "productId": "prod-1",
                "product": {"name": "Laptop", "sku": "LAP-1"},
                "quantity": {"standardQuantity": "1"},
            },
        ],
        "pickLines": [
            {
                "productId": "prod-1",
                "product": {"name": "Laptop", "sku": "LAP-1"},
                "quantity": {"standardQuantity": "1"},
            },
        ],
    }
    session.commit()

    pick_status_source = service.build_parent_remainder_pick_status_source(parent_order)
    picklist_view = service.build_parent_remainder_picklist_view(parent_order)

    assert pick_status_source is not None
    assert pick_status_source["lines"] == [
        {
            "productId": "prod-1",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": 1.0},
        },
    ]
    assert pick_status_source["pickLines"] == [
        {
            "productId": "prod-1",
            "product": {"name": "Laptop", "sku": "LAP-1"},
            "quantity": {"standardQuantity": 1.0},
        },
    ]

    assert picklist_view is not None
    assert picklist_view["pickLines"] == picklist_view["lines"]

    session.close()
    engine.dispose()


def test_parent_remainder_blocks_prep_actions_until_remaining_items_are_picked():
    """Remainder parent legs should not allow prep actions before their items are picked."""

    session, engine = _make_sqlite_session()
    session.execute(text("PRAGMA foreign_keys=ON"))

    parent_order = Order(
        id="order-parent-blocked",
        inflow_order_id="TH4001",
        inflow_sales_order_id="sales-order-4001",
        recipient_name="User Four",
        recipient_contact="user.four@example.com",
        delivery_location="Building 401",
        po_number="PO-4001",
        status=OrderStatus.PICKED.value,
        has_remainder="Y",
        inflow_data={
            "orderNumber": "TH4001",
            "lines": [
                {
                    "productId": "prod-1",
                    "description": "Dock",
                    "unitPrice": 120,
                    "quantity": {"standardQuantity": "2"},
                }
            ],
            "pickLines": [],
            "packLines": [],
            "shipLines": [],
        },
    )
    child_order = Order(
        id="order-child-blocked",
        inflow_order_id="TH4001-P",
        inflow_sales_order_id="sales-order-4001",
        recipient_name="User Four",
        recipient_contact="user.four@example.com",
        delivery_location="Building 401",
        po_number="PO-4001",
        status=OrderStatus.PICKED.value,
        parent_order_id=parent_order.id,
        inflow_data={
            "orderNumber": "TH4001-P",
            "lines": [
                {
                    "productId": "prod-1",
                    "description": "Dock",
                    "unitPrice": 120,
                    "quantity": {"standardQuantity": "2"},
                }
            ],
            "pickLines": [
                {
                    "productId": "prod-1",
                    "description": "Dock",
                    "unitPrice": 120,
                    "quantity": {"standardQuantity": "2"},
                }
            ],
            "packLines": [],
            "shipLines": [],
        },
    )

    session.add(parent_order)
    session.commit()
    session.add(child_order)
    session.commit()
    parent_order.remainder_order_id = child_order.id
    session.commit()

    try:
        service = OrderService(session)

        assert service._parent_remainder_has_unpicked_items(parent_order) is True

        with pytest.raises(
            ValidationError, match="remainder leg still has items waiting to be picked"
        ):
            service.mark_asset_tagged(
                parent_order.id, ["TAG-1"], technician="tech@example.com"
            )

        with pytest.raises(
            ValidationError, match="remainder leg still has items waiting to be picked"
        ):
            service.generate_picklist(
                parent_order.id, generated_by="tech@example.com"
            )

        with pytest.raises(
            ValidationError, match="remainder leg still has items waiting to be picked"
        ):
            service.send_order_details_email(
                parent_order.id, generated_by="tech@example.com"
            )

        parent_order.picklist_generated_at = datetime.utcnow()
        session.commit()

        with pytest.raises(
            ValidationError, match="remainder leg still has items waiting to be picked"
        ):
            service.submit_qa(
                parent_order.id,
                qa_data={
                    "orderNumber": "TH4001",
                    "technician": "tech",
                    "qaSignature": "tech",
                    "method": "Delivery",
                    "verifyAssetTagSerialMatch": True,
                    "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved": True,
                    "verifyPackagedProperly": True,
                    "verifyPackingSlipSerialsMatch": True,
                    "verifyBoxesLabeledCorrectly": True,
                },
                technician="tech@example.com",
            )
    finally:
        session.close()
        engine.dispose()


def test_create_order_from_inflow_overrides_delivery_location_to_vidi_from_custom_field():
    """Orders with the veterinary medicine custom field should store VIDI."""

    session, engine = _make_sqlite_session()

    try:
        service = OrderService(session)
        incoming_payload = {
            "orderNumber": "THVIDI001",
            "salesOrderId": "sales-order-vidi-1",
            "contactName": "Vet Med User",
            "email": "vetmed@example.com",
            "poNumber": "PO-VIDI-1",
            "shippingAddress": {
                "address1": "123 Main St",
                "address2": "",
                "city": "College Station",
                "stateProvince": "TX",
                "postalCode": "77843",
            },
            "customFields": {
                "custom1": "TAMU - College of Veterinary Medicine",
            },
            "lines": [],
            "pickLines": [],
            "packLines": [],
            "shipLines": [],
        }

        created = service.create_order_from_inflow(incoming_payload)
        session.refresh(created)

        assert created.delivery_location == "VIDI"
        assert created.inflow_data["customFields"]["custom1"] == "TAMU - College of Veterinary Medicine"
    finally:
        session.close()
        engine.dispose()


def test_create_order_from_inflow_overrides_delivery_location_to_zach_from_contact_name():
    """Orders for Takoda Powell should store ZACH."""

    session, engine = _make_sqlite_session()

    try:
        service = OrderService(session)
        incoming_payload = {
            "orderNumber": "THZACH001",
            "salesOrderId": "sales-order-zach-1",
            "contactName": "TAKODA POWELL",
            "email": "takoda@example.com",
            "poNumber": "PO-ZACH-1",
            "shippingAddress": {
                "address1": "500 Example Rd",
                "address2": "",
                "city": "College Station",
                "stateProvince": "TX",
                "postalCode": "77843",
            },
            "customFields": {},
            "lines": [],
            "pickLines": [],
            "packLines": [],
            "shipLines": [],
        }

        created = service.create_order_from_inflow(incoming_payload)
        session.refresh(created)

        assert created.delivery_location == "ZACH"
        assert created.recipient_name == "TAKODA POWELL"
    finally:
        session.close()
        engine.dispose()


def test_create_order_from_inflow_prefers_address_resolution_over_zach_contact_override():
    """Takoda Powell should keep a concrete address-derived mapping instead of forcing ZACH."""

    session, engine = _make_sqlite_session()

    try:
        service = OrderService(session)
        incoming_payload = {
            "orderNumber": "THZACH002",
            "salesOrderId": "sales-order-zach-2",
            "contactName": "TAKODA POWELL",
            "email": "takoda@example.com",
            "poNumber": "PO-ZACH-2",
            "shippingAddress": {
                "address1": "4220 TAMU",
                "address2": "ALLEN BLDG ROOM 2004A",
                "city": "College Station",
                "stateProvince": "TX",
                "postalCode": "77845",
            },
            "customFields": {},
            "lines": [],
            "pickLines": [],
            "packLines": [],
            "shipLines": [],
        }

        created = service.create_order_from_inflow(incoming_payload)
        session.refresh(created)

        assert created.delivery_location == "ALLEN"
        assert created.recipient_name == "TAKODA POWELL"
    finally:
        session.close()
        engine.dispose()

    print("[PASS] Parent remainder prep actions are blocked until remaining items are picked")


def test_generate_picklist_raises_when_sharepoint_upload_fails():
    """Picklist generation should fail if SharePoint upload is unavailable."""

    session, engine = _make_sqlite_session()
    order = Order(
        id="order-picklist-1",
        inflow_order_id="TH000140",
        inflow_sales_order_id="sales-order-140",
        recipient_name="User One",
        recipient_contact="user.one@example.com",
        delivery_location="Building 101",
        po_number="PO-0140",
        status=OrderStatus.PICKED.value,
        tagged_by="tech@example.com",
        inflow_data={
            "orderNumber": "TH000140",
            "contactName": "User One",
            "email": "user.one@example.com",
            "shippingAddress": {"address1": "123 Example St"},
            "customFields": {"custom4": "UIN-1"},
            "lines": [
                {
                    "productId": "prod-1",
                    "product": {"name": "Dock", "sku": "DOCK-1"},
                    "quantity": {"standardQuantity": "1"},
                }
            ],
            "pickLines": [
                {
                    "productId": "prod-1",
                    "product": {"name": "Dock", "sku": "DOCK-1"},
                    "quantity": {"standardQuantity": "1"},
                }
            ],
        },
    )
    session.add(order)
    session.commit()

    from app.services.order_service import OrderService

    service = OrderService(session)

    class FakeSharePointService:
        is_enabled = True

        def upload_pdf(self, pdf_path: str, subfolder: str, filename: str) -> str:
            raise RuntimeError("sharepoint offline")

    def fake_generate_picklist_pdf(self, inflow_data, output_path):
        Path(output_path).write_bytes(b"%PDF-1.4 fake picklist\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        service._local_doc_path = lambda category, filename: Path(tmpdir) / category / filename  # type: ignore[method-assign]

        with patch("app.services.sharepoint_service.get_sharepoint_service", return_value=FakeSharePointService()):
            with patch(
                "app.services.picklist_service.PicklistService.generate_picklist_pdf",
                new=fake_generate_picklist_pdf,
            ):
                with patch(
                    "app.services.order_service.SystemSettingService.is_setting_enabled",
                    return_value=False,
                ):
                    with patch(
                        "app.services.order_service.SystemSettingService.get_setting",
                        return_value="false",
                    ):
                        with patch.object(service, "_send_order_details_email", return_value=True):
                            try:
                                service.generate_picklist(
                                    order.id,
                                    generated_by="tech@example.com",
                                    generated_by_display="tech@example.com",
                                )
                                raised = False
                            except RuntimeError as exc:
                                raised = True
                                assert "sharepoint offline" in str(exc)

        local_path = Path(tmpdir) / "picklists" / "TH000140.pdf"
        assert local_path.exists()
        assert local_path.read_bytes().startswith(b"%PDF-1.4 fake picklist")

    assert raised is True
    session.close()
    engine.dispose()


def test_generate_picklist_auto_print_enqueues_only_once():
    """Auto-print should not enqueue the same picklist twice during one generation request."""

    session, engine = _make_sqlite_session()
    order = Order(
        id="order-picklist-2",
        inflow_order_id="TH000141",
        inflow_sales_order_id="sales-order-141",
        recipient_name="User Two",
        recipient_contact="user.two@example.com",
        delivery_location="Building 202",
        po_number="PO-0141",
        status=OrderStatus.PICKED.value,
        tagged_by="tech@example.com",
        inflow_data={
            "orderNumber": "TH000141",
            "contactName": "User Two",
            "email": "user.two@example.com",
            "shippingAddress": {"address1": "202 Example St"},
            "customFields": {"custom4": "UIN-2"},
            "lines": [
                {
                    "productId": "prod-1",
                    "product": {"name": "Dock", "sku": "DOCK-1"},
                    "quantity": {"standardQuantity": "1"},
                    "unitPrice": "12.50",
                }
            ],
            "pickLines": [
                {
                    "productId": "prod-1",
                    "product": {"name": "Dock", "sku": "DOCK-1"},
                    "quantity": {"standardQuantity": "1"},
                }
            ],
            "packLines": [],
            "shipLines": [],
        },
    )
    session.add(order)
    session.commit()

    from app.models.print_job import PrintJob
    from app.services.order_service import OrderService

    service = OrderService(session)

    class FakeSharePointService:
        is_enabled = True

        def upload_pdf(self, pdf_path: str, subfolder: str, filename: str) -> str:
            return f"sharepoint://{subfolder}/{filename}"

        def upload_file(self, pdf_content, subfolder: str, filename: str) -> str:
            return f"sharepoint://{subfolder}/{filename}"

    def fake_generate_picklist_pdf(self, inflow_data, output_path):
        Path(output_path).write_bytes(b"%PDF-1.4 fake picklist\n")

    def fake_generate_order_details_pdf(inflow_data):
        return b"%PDF-1.4 fake order details\n"

    with tempfile.TemporaryDirectory() as tmpdir:
        service._local_doc_path = lambda category, filename: Path(tmpdir) / category / filename  # type: ignore[method-assign]

        with patch("app.services.sharepoint_service.get_sharepoint_service", return_value=FakeSharePointService()):
            with patch(
                "app.services.picklist_service.PicklistService.generate_picklist_pdf",
                new=fake_generate_picklist_pdf,
            ):
                fake_pdf_module = SimpleNamespace(
                    pdf_service=SimpleNamespace(generate_order_details_pdf=fake_generate_order_details_pdf)
                )
                fake_email_module = SimpleNamespace(
                    email_service=SimpleNamespace(
                        is_configured=lambda: True,
                        send_order_details_email=lambda **kwargs: True,
                    )
                )
                with patch.dict(
                    sys.modules,
                    {
                        "app.services.pdf_service": fake_pdf_module,
                        "app.services.email_service": fake_email_module,
                    },
                ):
                    with patch(
                        "app.services.order_service.SystemSettingService.is_setting_enabled",
                        return_value=False,
                    ):
                        with patch(
                            "app.services.order_service.SystemSettingService.get_setting",
                            return_value="true",
                        ):
                            result = service.generate_picklist(
                                order.id,
                                generated_by="tech@example.com",
                                generated_by_display="Tech Example",
                                create_partial_leg=False,
                            )

    jobs = session.query(PrintJob).filter(PrintJob.order_id == order.id).all()

    assert result.picklist_path == "sharepoint://picklists/TH000141.pdf"
    assert result.order_details_email_status == OrderService.ORDER_DETAILS_EMAIL_SENT
    assert len(jobs) == 1
    assert jobs[0].trigger_source == "automatic"
    assert jobs[0].requested_by == "Tech Example"

    session.close()
    engine.dispose()


def test_order_details_generated_pdf_is_uploaded_to_sharepoint():
    """Generated order-details PDFs should be uploaded and store the SharePoint URL."""

    class FakeSharePointService:
        is_enabled = True

        def __init__(self) -> None:
            self.upload_calls: list[tuple[bytes, str, str]] = []

        def download_file(self, subfolder: str, filename: str) -> bytes | None:
            return None

        def upload_file(self, content: bytes, subfolder: str, filename: str) -> str:
            self.upload_calls.append((content, subfolder, filename))
            return f"sharepoint://{subfolder}/{filename}"

    fake_sp_service = FakeSharePointService()

    order = SimpleNamespace(
        id="order-5",
        inflow_order_id="TH1005",
        recipient_contact="user@example.com",
        recipient_name="User",
        inflow_data={"orderNumber": "TH1005", "lines": [], "pickLines": [], "packLines": []},
        order_details_path=None,
        order_details_generated_at=None,
    )

    db = SimpleNamespace(commit=lambda: None, refresh=lambda _order: None)
    from app.services.order_service import OrderService

    service = OrderService(db=cast(Any, db))

    with tempfile.TemporaryDirectory() as tmpdir:
        service._storage_path = lambda *parts: Path(tmpdir).joinpath(*parts)  # type: ignore[method-assign]

        with patch(
            "app.services.sharepoint_service.get_sharepoint_service",
            return_value=fake_sp_service,
        ):
            fake_pdf_module = types.ModuleType("app.services.pdf_service")
            fake_pdf_module.pdf_service = SimpleNamespace(
                generate_order_details_pdf=lambda payload: b"fresh-pdf"
            )
            with patch.dict(
                sys.modules,
                {"app.services.pdf_service": fake_pdf_module},
            ):
                with patch(
                    "app.services.email_service.email_service.is_configured",
                    return_value=False,
                ):
                    success = service._send_order_details_email(order)

    assert success is False
    assert fake_sp_service.upload_calls == [(b"fresh-pdf", "order-details", "TH1005.pdf")]
    assert order.order_details_path == "sharepoint://order-details/TH1005.pdf"
    assert order.order_details_generated_at is not None
    assert order.order_details_email_status == OrderService.ORDER_DETAILS_EMAIL_NOT_SENT
    assert order.order_details_email_status_updated_at is not None
    print("[PASS] Generated order-details PDFs upload to SharePoint")


if __name__ == "__main__":
    print("Running partial-order workflow tests...")
    print()

    test_fulfill_sales_order_appends_new_partial_shipment_lines()
    test_asset_tag_serials_only_include_unshipped_remaining_items()
    test_partial_order_remainder_creation_links_parent_and_child()
    test_partial_order_details_regenerate_instead_of_reusing_sharepoint_pdf()
    test_order_details_generated_pdf_is_uploaded_to_sharepoint()

    print()
    print("[SUCCESS] All partial-order workflow tests passed!")
