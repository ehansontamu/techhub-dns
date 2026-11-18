import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class PrintJobStatus:
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


class PrintJobTriggerSource:
    AUTOMATIC = "automatic"
    MANUAL = "manual"


class PrintJobDocumentType:
    PICKLIST = "picklist"
    SHIPPING_LABEL = "shipping_label"


class PrintJob(Base):
    __tablename__ = "print_jobs"

    __table_args__ = (
        Index("ix_print_jobs_status_created_at", "status", "created_at"),
        Index(
            "ix_print_jobs_order_document_created_at",
            "order_id",
            "document_type",
            "created_at",
        ),
        Index("ix_print_jobs_claim_expires_at", "claim_expires_at"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id = Column(
        String(36),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_type = Column(
        String(50), nullable=False, default=PrintJobDocumentType.PICKLIST
    )
    status = Column(String(50), nullable=False, default=PrintJobStatus.PENDING)
    trigger_source = Column(
        String(50), nullable=False, default=PrintJobTriggerSource.AUTOMATIC
    )
    requested_by = Column(String(255), nullable=True)
    file_path = Column(String(500), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0)
    claimed_at = Column(DateTime, nullable=True)
    claim_expires_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    order = relationship("Order", back_populates="print_jobs")
