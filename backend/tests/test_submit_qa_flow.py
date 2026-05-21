import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.models.order import Order, OrderStatus
from app.services.order_service import OrderService


def test_submit_qa_sets_completed_at_and_transitions_to_pre_delivery(tmp_path, monkeypatch):
    mock_db = MagicMock()
    service = OrderService(mock_db)

    # Keep file IO local and deterministic.
    monkeypatch.setattr("app.services.order_service.settings.local_document_storage", str(tmp_path))

    # Stub SharePoint so the test stays offline.
    fake_sp = MagicMock()
    fake_sp.is_enabled = True
    fake_sp.upload_file.return_value = "https://sharepoint.example/qa/TH123.json"
    monkeypatch.setattr("app.services.sharepoint_service.get_sharepoint_service", lambda: fake_sp)

    # Avoid audit side effects while still exercising submit_qa -> transition_status.
    monkeypatch.setattr("app.services.order_service.AuditService.log_order_action", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.order_service.OrderService._prep_steps_complete", lambda self, order: True)

    order = MagicMock(spec=Order)
    order.id = "test-order-id"
    order.inflow_order_id = "TH123"
    order.status = OrderStatus.QA.value
    order.tagged_at = datetime.utcnow()
    order.picklist_generated_at = datetime.utcnow()
    order.picklist_generated_by = "picker@example.com"
    order.qa_completed_at = None
    order.qa_method = None
    order.inflow_data = {
        "shippingAddress": {
            "city": "Bryan",
            "address1": "123 Main St",
            "address2": "",
            "stateProvince": "TX",
            "postalCode": "77801",
        }
    }

    mock_db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = order

    # Directly create the local QA JSON file in a temp directory.
    qa_data = {
        "orderNumber": order.inflow_order_id,
        "technician": "Hunter",
        "qaSignature": "Sig",
        "verifyAssetTagSerialMatch": True,
        "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved": True,
        "verifyPackagedProperly": True,
        "verifyPackingSlipSerialsMatch": True,
        "verifyBoxesLabeledCorrectly": True,
    }

    result = service.submit_qa(
        order.id,
        qa_data,
        technician="Test Tech",
        technician_identifier="qa-tech@example.com",
    )

    assert order.qa_completed_at is not None
    assert order.qa_method == "Delivery"
    assert result.status == OrderStatus.PRE_DELIVERY.value
    assert Path(tmp_path / "qa" / "TH123.json").exists()


def test_submit_qa_routes_shipping_orders_to_shipping(tmp_path, monkeypatch):
    mock_db = MagicMock()
    service = OrderService(mock_db)

    monkeypatch.setattr("app.services.order_service.settings.local_document_storage", str(tmp_path))

    fake_sp = MagicMock()
    fake_sp.is_enabled = True
    fake_sp.upload_file.return_value = "https://sharepoint.example/qa/TH123.json"
    monkeypatch.setattr("app.services.sharepoint_service.get_sharepoint_service", lambda: fake_sp)

    monkeypatch.setattr("app.services.order_service.AuditService.log_order_action", lambda *args, **kwargs: None)

    order = MagicMock(spec=Order)
    order.id = "test-order-id"
    order.inflow_order_id = "TH123"
    order.status = OrderStatus.QA.value
    order.tagged_at = datetime.utcnow()
    order.picklist_generated_at = datetime.utcnow()
    order.qa_completed_at = None
    order.qa_method = None
    order.delivery_run_id = None
    order.inflow_data = {
        "shippingAddress": {
            "city": "Houston",
            "address1": "500 Ship Ave",
            "address2": "",
            "stateProvince": "TX",
            "postalCode": "77001",
        }
    }

    mock_db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = order

    qa_data = {
        "orderNumber": order.inflow_order_id,
        "technician": "Hunter",
        "qaSignature": "Sig",
        "verifyAssetTagSerialMatch": True,
        "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved": True,
        "verifyPackagedProperly": True,
        "verifyPackingSlipSerialsMatch": True,
        "verifyBoxesLabeledCorrectly": True,
    }

    result = service.submit_qa(order.id, qa_data, technician="Test Tech")

    assert order.qa_completed_at is not None
    assert order.qa_method == "Shipping"
    assert result.status == OrderStatus.SHIPPING.value
    assert Path(tmp_path / "qa" / "TH123.json").exists()


def test_submit_qa_allows_parent_partial_leg(tmp_path, monkeypatch):
    mock_db = MagicMock()
    service = OrderService(mock_db)

    monkeypatch.setattr("app.services.order_service.settings.local_document_storage", str(tmp_path))

    fake_sp = MagicMock()
    fake_sp.is_enabled = True
    fake_sp.upload_file.return_value = "https://sharepoint.example/qa/TH123.json"
    monkeypatch.setattr("app.services.sharepoint_service.get_sharepoint_service", lambda: fake_sp)

    order = MagicMock(spec=Order)
    order.id = "test-order-id"
    order.inflow_order_id = "TH123"
    order.status = OrderStatus.QA.value
    order.tagged_at = datetime.utcnow()
    order.picklist_generated_at = datetime.utcnow()
    order.picklist_generated_by = "picker@example.com"
    order.qa_completed_at = None
    order.qa_method = None
    order.parent_order_id = None
    order.remainder_order_id = "child-order-id"

    mock_db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = order

    monkeypatch.setattr("app.services.order_service.AuditService.log_order_action", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.order_service.OrderService._prep_steps_complete", lambda self, order: True)

    qa_data = {
        "method": "Delivery",
        "orderNumber": order.inflow_order_id,
        "technician": "Hunter",
        "qaSignature": "Sig",
        "verifyAssetTagSerialMatch": True,
        "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved": True,
        "verifyPackagedProperly": True,
        "verifyPackingSlipSerialsMatch": True,
        "verifyBoxesLabeledCorrectly": True,
    }

    result = service.submit_qa(
        order.id,
        qa_data,
        technician="Test Tech",
        technician_identifier="qa-tech@example.com",
    )

    assert order.qa_completed_at is not None
    assert order.qa_method == "Delivery"
    assert result.status == OrderStatus.PRE_DELIVERY.value
    assert Path(tmp_path / "qa" / "TH123.json").exists()


def test_submit_qa_blocks_picker_from_qcing_own_order(tmp_path, monkeypatch):
    mock_db = MagicMock()
    service = OrderService(mock_db)

    monkeypatch.setattr("app.services.order_service.settings.local_document_storage", str(tmp_path))

    order = MagicMock(spec=Order)
    order.id = "test-order-id"
    order.inflow_order_id = "TH123"
    order.status = OrderStatus.QA.value
    order.tagged_at = datetime.utcnow()
    order.picklist_generated_at = datetime.utcnow()
    order.picklist_generated_by = "picker@example.com"
    order.qa_completed_at = None
    order.qa_method = None

    mock_db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = order

    qa_data = {
        "method": "Delivery",
        "orderNumber": order.inflow_order_id,
        "technician": "Picker Person",
        "qaSignature": "Sig",
        "verifyAssetTagSerialMatch": True,
        "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved": True,
        "verifyPackagedProperly": True,
        "verifyPackingSlipSerialsMatch": True,
        "verifyBoxesLabeledCorrectly": True,
    }

    from app.utils.exceptions import ValidationError

    try:
        service.submit_qa(
            order.id,
            qa_data,
            technician="Picker Person",
            technician_identifier="picker@example.com",
        )
    except ValidationError as exc:
        assert "someone other than the person who picked" in str(exc)
    else:
        raise AssertionError("Expected picker to be blocked from QA")
