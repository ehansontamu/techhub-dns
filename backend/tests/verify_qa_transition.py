import sys
from unittest.mock import MagicMock, patch
from datetime import datetime
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.services.order_service import OrderService
from app.models.order import Order, OrderStatus

def test_qa_transition_delivery():
    print("Testing QA transition (Delivery)...")

    msg = run_test("Delivery", OrderStatus.PRE_DELIVERY.value)
    print(msg)

def test_qa_transition_shipping():
    print("Testing QA transition (Shipping)...")

    msg = run_test("Shipping", OrderStatus.SHIPPING.value)
    print(msg)

def run_test(method, expected_status):
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
    mock_order.qa_method = None

    # Mock query return
    mock_db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = mock_order

    # QA Data
    qa_data = {
        "method": method,
        "orderNumber": mock_order.inflow_order_id,
        "technician": "Hunter",
        "qaSignature": "Sig",
        "verifyAssetTagSerialMatch": True,
        "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved": True,
        "verifyPackagedProperly": True,
        "verifyPackingSlipSerialsMatch": True,
        "verifyBoxesLabeledCorrectly": True,
    }

    # Patch internal methods to avoid side effects
    with patch.object(service, '_storage_path') as mock_path, \
         patch('app.services.order_service.json') as mock_json:

        mock_path.return_value.mkdir.return_value = None
        mock_path.return_value.__truediv__.return_value = MagicMock()

        # We also need to mock transition_status call inside submit_qa because implementation calls it
        # Actually, let's allow it to be called but mock the DB operations inside it if needed
        # Since we mocked the DB, transition_status should work fine as long as we mock the validations if they are complex
        # But wait, transition_status calls _is_valid_transition which is pure logic.
        # It also does checks.

        # We need to make sure _is_shipping_order returns True for Shipping method if we want to test Shipping transition?
        # Actually, transition_status checks:
        # if new_status == OrderStatus.SHIPPING:
        #     if not self._is_shipping_order(order):
        #         raise ValidationError...

        if method == "Shipping":
            # Mock _is_shipping_order to return True
            service._is_shipping_order = MagicMock(return_value=True)
        else:
            service._is_shipping_order = MagicMock(return_value=False)

        # Also need to ensure _prep_steps_complete returns True for PRE_DELIVERY
        service._prep_steps_complete = MagicMock(return_value=True)

        try:
            service.submit_qa(mock_order.id, qa_data, technician="Test Tech")
        except Exception as e:
            return f"[FAIL] Error calling submit_qa: {e}"

    if mock_order.status == expected_status:
        return f"[SUCCESS] Status changed to {mock_order.status}."
    else:
        return f"[FAIL] Status is {mock_order.status}, expected {expected_status}."

if __name__ == "__main__":
    test_qa_transition_delivery()
    print("-" * 20)
    test_qa_transition_shipping()
