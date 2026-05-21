import sys
from unittest.mock import MagicMock, patch
from datetime import datetime
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.services.order_service import OrderService
from app.models.order import Order, OrderStatus

def test_qa_transition_bug():
    print("reproducing QA transition bug...")

    # Mock DB Session
    mock_db = MagicMock()

    # Create OrderService
    service = OrderService(mock_db)

    # Setup Mock Order
    mock_order = MagicMock(spec=Order)
    mock_order.id = "test-order-id"
    mock_order.inflow_order_id = "TH123"
    mock_order.status = OrderStatus.QA.value
    mock_order.picklist_generated_at = datetime.utcnow()
    mock_order.qa_method = None # Initially none

    # Mock query return
    mock_db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_order

    # QA Data
    qa_data = {
        "method": "Delivery",
        "orderNumber": mock_order.inflow_order_id,
        "technician": "Hunter",
        "qaSignature": "Sig",
        "verifyAssetTagSerialMatch": True,
        "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved": True,
        "verifyPackagedProperly": True,
        "verifyPackingSlipSerialsMatch": True,
        "verifyBoxesLabeledCorrectly": True,
    }

    print(f"Initial Status: {mock_order.status}")

    # Patch internal methods to avoid side effects (file writing, sharepoint)
    with patch.object(service, '_storage_path') as mock_path, \
         patch('app.services.order_service.json') as mock_json:

        mock_path.return_value.mkdir.return_value = None
        mock_path.return_value.__truediv__.return_value = MagicMock() # file path

        try:
            service.submit_qa(mock_order.id, qa_data, technician="Test Tech")
        except Exception as e:
            print(f"Error calling submit_qa: {e}")
            return

    print(f"Status after submit_qa: {mock_order.status}")

    if mock_order.status == OrderStatus.PRE_DELIVERY.value:
        print("[FAIL] Bug not reproduced! Status changed to PRE_DELIVERY.")
    elif mock_order.status == OrderStatus.QA.value:
         print("[SUCCESS] Bug reproduced! Status remained QA.")
    else:
        print(f"[?] Unexpected status: {mock_order.status}")

if __name__ == "__main__":
    test_qa_transition_bug()
