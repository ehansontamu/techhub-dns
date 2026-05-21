import uuid
from datetime import datetime
import enum

from sqlalchemy import Column, String, DateTime, Enum as SQLEnum, Index
from sqlalchemy.orm import relationship

from app.database import Base


class VehicleEnum(enum.Enum):
    VAN = "van"
    GOLF_CART = "golf_cart"


class DeliveryRunStatus(enum.Enum):
    ACTIVE = "Active"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class DeliveryRun(Base):
    __tablename__ = "delivery_runs"
    __table_args__ = (
        Index("ix_delivery_runs_created_at", "created_at"),
        Index("ix_delivery_runs_status_created_at_id", "status", "created_at", "id"),
        Index("ix_delivery_runs_vehicle_status_created_at_id", "vehicle", "status", "created_at", "id"),
        Index("ix_delivery_runs_vehicle_status", "vehicle", "status"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    runner = Column(String(255), nullable=False)
    vehicle = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False, default=DeliveryRunStatus.ACTIVE.value)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Orders assigned to this run
    orders = relationship("Order", back_populates="delivery_run")
