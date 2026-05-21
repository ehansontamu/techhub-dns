#!/usr/bin/env python3
"""End-to-end smoke test for the partial-order workflow from split through delivery."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

backend_path = Path(__file__).parent.parent
sys.path.append(str(backend_path))

from app.database import Base
from app.models.delivery_run import DeliveryRunStatus
from app.models.order import Order, OrderStatus
from app.models.audit_log import AuditLog
from app.services.delivery_run_service import DeliveryRunService
from app.services.order_service import OrderService
from app.services.order_splitting import OrderSplittingService


def _make_sqlite_session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = session_factory()
    session.execute(text("PRAGMA foreign_keys=ON"))
    return session, engine


class _SmokeSharePointService:
    def __init__(self) -> None:
        self.is_enabled = True
        self.uploads: list[tuple[str, str]] = []

    def upload_pdf(self, content: bytes | str, subfolder: str, filename: str) -> str:
        self.uploads.append((subfolder, filename))
        return f"sharepoint://{subfolder}/{filename}"

    def upload_file(self, content: bytes | str, subfolder: str, filename: str) -> str:
        self.uploads.append((subfolder, filename))
        return f"sharepoint://{subfolder}/{filename}"

    def download_file(self, subfolder: str, filename: str):
        return None

    def get_file_url(self, subfolder: str, filename: str) -> str:
        return f"sharepoint://{subfolder}/{filename}"



def test_partial_order_smoke_split_to_delivery(monkeypatch):
    """Smoke the full partial-order chain: split -> docs -> QA -> delivery -> requeue."""

    session, engine = _make_sqlite_session()
    sharepoint = _SmokeSharePointService()

    try:
        monkeypatch.setattr(
            "app.services.order_service.settings.local_document_storage",
            str(Path(tempfile.gettempdir()) / "techhub-dns-partial-smoke"),
        )
        monkeypatch.setattr(
            "app.services.order_service.SystemSettingService.is_setting_enabled",
            lambda *args, **kwargs: False,
        )
        monkeypatch.setattr(
            "app.services.order_service.SystemSettingService.get_setting",
            lambda *args, **kwargs: "false",
        )
        monkeypatch.setattr(
            "app.services.sharepoint_service.get_sharepoint_service",
            lambda: sharepoint,
        )
        monkeypatch.setattr(
            "app.services.audit_service.AuditService.log_order_action",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "app.services.audit_service.AuditService.log_delivery_run_action",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "app.services.order_service.OrderService._record_status_history",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "app.services.delivery_run_service.DeliveryRunService._record_status_history",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "app.services.email_service.email_service.is_configured",
            lambda: True,
        )
        monkeypatch.setattr(
            "app.services.email_service.email_service.send_order_details_email",
            lambda *args, **kwargs: True,
        )
        monkeypatch.setattr(
            "app.services.picklist_service.PicklistService.generate_picklist_pdf",
            lambda self, inflow_data, output_path: Path(output_path).write_bytes(
                b"%PDF-1.4\n%partial-order-smoke\n"
            ),
        )
        fake_pdf_service = ModuleType("app.services.pdf_service")
        fake_pdf_service.pdf_service = SimpleNamespace(
            generate_order_details_pdf=lambda inflow_data: b"%PDF-1.4\n%order-details-smoke\n"
        )
        monkeypatch.setitem(sys.modules, "app.services.pdf_service", fake_pdf_service)
        monkeypatch.setattr(
            "app.services.inflow_service.InflowService.update_order_remarks_sync",
            lambda *args, **kwargs: None,
        )

        parent_order = Order(
            id="order-parent-smoke",
            inflow_order_id="TH9001",
            inflow_sales_order_id="sales-order-smoke-1",
            recipient_name="Smoke Tester",
            recipient_contact="smoke.tester@example.com",
            delivery_location="Building 9001",
            po_number="PO-9001",
            status=OrderStatus.PICKED.value,
            inflow_data={
                "orderNumber": "TH9001",
                "contactName": "Smoke Tester",
                "email": "smoke.tester@example.com",
                "shippingAddress": {"address1": "9001 Example St"},
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
            },
        )
        session.add(parent_order)
        session.commit()

        order_service = OrderService(session)

        generated_child = order_service.generate_picklist(
            parent_order.id,
            generated_by="runner@example.com",
            generated_by_display="Runner User",
            create_partial_leg=True,
        )

        session.refresh(parent_order)
        session.refresh(generated_child)

        assert generated_child.parent_order_id == parent_order.id
        assert parent_order.remainder_order_id == generated_child.id
        assert parent_order.has_remainder == "Y"
        assert generated_child.inflow_order_id == "TH9001-P"
        assert generated_child.status == OrderStatus.QA.value
        assert generated_child.picklist_path == "sharepoint://picklists/TH9001-P.pdf"
        assert generated_child.order_details_path == "sharepoint://order-details/TH9001-P.pdf"
        assert parent_order.picklist_path is None
        assert parent_order.order_details_path is None
        assert sharepoint.uploads[:2] == [
            ("picklists", "TH9001-P.pdf"),
            ("order-details", "TH9001-P.pdf"),
        ]

        parent_order.inflow_data = {
            "orderNumber": "TH9001",
            "contactName": "Smoke Tester",
            "email": "smoke.tester@example.com",
            "shippingAddress": {"address1": "9001 Example St"},
            "lines": [
                {
                    "productId": "prod-1",
                    "description": "Laptop",
                    "quantity": {"standardQuantity": "1"},
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
        }
        session.commit()

        recursive_child = order_service.generate_picklist(
            parent_order.id,
            generated_by="runner@example.com",
            generated_by_display="Runner User",
            create_partial_leg=False,
        )

        session.refresh(parent_order)
        session.refresh(generated_child)
        session.refresh(recursive_child)

        assert recursive_child.parent_order_id == parent_order.id
        assert recursive_child.inflow_order_id == "TH9001-P2"
        assert parent_order.remainder_order_id == recursive_child.id
        assert parent_order.inflow_data["lines"] == [
            {
                "productId": "prod-2",
                "description": "Dock",
                "quantity": {"standardQuantity": 1.0},
            }
        ]
        assert parent_order.inflow_data["pickLines"] == []
        assert generated_child.picklist_path == "sharepoint://picklists/TH9001-P.pdf"
        assert recursive_child.picklist_path == "sharepoint://picklists/TH9001-P2.pdf"
        assert recursive_child.order_details_path == "sharepoint://order-details/TH9001-P2.pdf"
        assert sharepoint.uploads[2:4] == [
            ("picklists", "TH9001-P2.pdf"),
            ("order-details", "TH9001-P2.pdf"),
        ]

        qa_payload = {
            "orderNumber": recursive_child.inflow_order_id,
            "technician": "Runner User",
            "qaSignature": "Smoke Signature",
            "method": "Delivery",
            "verifyAssetTagSerialMatch": True,
            "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved": True,
            "verifyPackagedProperly": True,
            "verifyPackingSlipSerialsMatch": True,
            "verifyBoxesLabeledCorrectly": True,
        }
        qa_result = order_service.submit_qa(
            recursive_child.id,
            qa_payload,
            technician="Runner User",
        )

        session.refresh(qa_result)
        assert qa_result.status == OrderStatus.PRE_DELIVERY.value
        assert qa_result.qa_completed_at is not None
        assert qa_result.qa_path == "sharepoint://qa/TH9001-P2.json"
        assert qa_result.qa_method == "Delivery"
        assert sharepoint.uploads[4] == ("qa", "TH9001-P2.json")

        delivery_service = DeliveryRunService(session)
        delivery_service._get_authenticated_actor = lambda: (
            "runner-1",
            "Runner User",
            "runner@example.com",
        )
        delivery_service._get_active_checkout = lambda vehicle: SimpleNamespace(
            checked_out_by_user_id="runner-1",
            checked_out_by="Runner User",
            checkout_type="delivery_run",
            purpose=None,
        )

        run = delivery_service.create_run_for_current_user([recursive_child.id], vehicle="van")
        session.refresh(run)
        session.refresh(recursive_child)
        assert run.status == DeliveryRunStatus.ACTIVE.value
        assert recursive_child.status == OrderStatus.IN_DELIVERY.value
        assert recursive_child.delivery_run_id == run.id

        delivered = order_service.transition_status(
            recursive_child.id,
            OrderStatus.DELIVERED,
            changed_by="Runner User",
            reason="Delivered during smoke test",
        )
        session.refresh(delivered)
        assert delivered.status == OrderStatus.DELIVERED.value

        partial_delivery_payload = {
            **recursive_child.inflow_data,
            "packLines": [
                {
                    "productId": "prod-laptop",
                    "quantity": {"standardQuantity": "1"},
                }
            ],
            "shipLines": [
                {
                    "salesOrderShipLineId": "ship-1",
                    "carrier": "TechHub",
                    "containers": ["DELIVERY-TH9001-P2-1"],
                }
            ],
        }
        monkeypatch.setattr(
            "app.services.inflow_service.InflowService.fulfill_sales_order",
            AsyncMock(return_value=partial_delivery_payload),
        )

        completed_run = delivery_service.finish_run(
            run.id,
            user_id="runner-1",
            create_remainders=True,
        )

        session.refresh(completed_run)
        session.refresh(recursive_child)

        assert completed_run.status == DeliveryRunStatus.COMPLETED.value
        assert recursive_child.status == OrderStatus.DELIVERED.value
        assert recursive_child.delivery_run_id == run.id
        assert recursive_child.picklist_generated_at is not None
        assert recursive_child.picklist_path is not None
        assert recursive_child.qa_completed_at is not None
        assert recursive_child.qa_path is not None
        assert recursive_child.order_details_path is not None
        assert recursive_child.qa_method == "Delivery"
        assert recursive_child.inflow_data == partial_delivery_payload
        assert parent_order.remainder_order_id == recursive_child.id

        print("[PASS] partial-order smoke chain split -> docs -> QA -> delivery -> delivered")
    finally:
        session.close()
        engine.dispose()





if __name__ == "__main__":
    raise SystemExit("Run this smoke test with pytest, not directly.")
