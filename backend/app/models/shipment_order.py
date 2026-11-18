"""Reserve every member order of a combined shipment exactly once."""
from sqlalchemy import Column, ForeignKey, String
from app.database import Base

class ShipmentOrder(Base):
    __tablename__ = "shipment_orders"
    order_id = Column(String(36), ForeignKey("orders.id"), primary_key=True)
    shipment_id = Column(String(36), ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True)
