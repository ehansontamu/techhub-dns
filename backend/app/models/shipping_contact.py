"""Local recipient copy imported from a FedEx address-book CSV export."""
from datetime import datetime
from sqlalchemy import Column, DateTime, JSON, String, Text
from app.database import Base

class ShippingContact(Base):
    __tablename__ = "shipping_contacts"
    id = Column(String(64), primary_key=True)
    label = Column(String(500), nullable=False)
    recipient = Column(JSON, nullable=False)
    search_text = Column(Text, nullable=False)
    imported_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    imported_by = Column(String(255), nullable=True)
