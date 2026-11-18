"""Packing drafts and durable FedEx results, independent of order handoff status."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text

from app.database import Base


class ShippingProduct(Base):
    __tablename__ = "shipping_products"

    product_id = Column(String(255), primary_key=True)
    name = Column(String(500), nullable=False)
    sku = Column(String(255), nullable=True)
    weight_lb = Column(Numeric(10, 3), nullable=True)
    updated_by = Column(String(255), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class Shipment(Base):
    __tablename__ = "shipments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id = Column(String(36), ForeignKey("orders.id"), nullable=False, unique=True)
    status = Column(String(30), nullable=False, default="draft")
    revision = Column(Integer, nullable=False, default=1)
    environment = Column(String(20), nullable=True)
    draft = Column(JSON, nullable=False)
    # Preserve the exact request and response before attempting label extraction.
    request_payload = Column(JSON, nullable=True)
    carrier_response = Column(JSON, nullable=True)
    packages = Column(JSON, nullable=True)
    master_tracking_number = Column(String(100), nullable=True)
    last_error = Column(Text, nullable=True)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
