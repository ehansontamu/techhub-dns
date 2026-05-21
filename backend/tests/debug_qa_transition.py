import sys
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import datetime

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from app.database import Base
from app.models.order import Order, OrderStatus
from app.services.order_service import OrderService

def debug_setup():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()

def test_scenario(name, order_status, has_tagged_at=True, has_picklist=True, method="Delivery"):
    print(f"\n--- Testing Scenario: {name} ---")
    db = debug_setup()
    service = OrderService(db)

    order = Order(
        inflow_order_id=f"TEST-{name}",
        status=order_status,
        tagged_at=datetime.utcnow() if has_tagged_at else None,
        picklist_generated_at=datetime.utcnow() if has_picklist else None
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    qa_data = {
        "method": method,
        "orderNumber": order.inflow_order_id,
        "technician": "Hunter",
        "qaSignature": "Sig",
        "verifyAssetTagSerialMatch": True,
        "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved": True,
        "verifyPackagedProperly": True,
        "verifyPackingSlipSerialsMatch": True,
        "verifyBoxesLabeledCorrectly": True
    }

    try:
        service.submit_qa(order.id, qa_data, technician="Hunter")
        print(f"Resulting status: {order.status}")
    except Exception as e:
        print(f"Caught exception: {e}")
        if hasattr(e, 'details'):
            print(f"Details: {e.details}")

if __name__ == "__main__":
    # 1. Normal successful flow
    test_scenario("Success Delivery", OrderStatus.QA.value)
    test_scenario("Success Shipping", OrderStatus.QA.value, method="Shipping")

    # 2. Missing tagged_at
    test_scenario("Missing tagged_at", OrderStatus.QA.value, has_tagged_at=False)

    # 3. Missing picklist (Submit QA should check this)
    test_scenario("Missing picklist", OrderStatus.QA.value, has_picklist=False)

    # 4. Wrong initial status
    test_scenario("Wrong initial status (Picked)", OrderStatus.PICKED.value)
    test_scenario("Wrong initial status (In-Delivery)", OrderStatus.IN_DELIVERY.value)
