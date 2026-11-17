from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session, selectinload

from app.models.order import Order
from app.models.print_job import (
    PrintJob,
    PrintJobDocumentType,
    PrintJobStatus,
    PrintJobTriggerSource,
)
from app.services.audit_service import AuditService
from app.utils.exceptions import ConflictError, NotFoundError, ValidationError
from app.utils.timezone import to_utc_iso_z

PRINT_JOB_ROOM = "print_jobs"
PRINT_JOB_AVAILABLE_EVENT = "print_job_available"


class PrintJobService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def serialize_job(job: PrintJob) -> dict[str, Any]:
        order = job.order
        return {
            "id": job.id,
            "order_id": job.order_id,
            "order_inflow_order_id": order.inflow_order_id if order else None,
            "document_type": job.document_type,
            "status": job.status,
            "trigger_source": job.trigger_source,
            "requested_by": job.requested_by,
            "file_path": job.file_path,
            "attempt_count": job.attempt_count,
            "claimed_at": to_utc_iso_z(job.claimed_at),
            "claim_expires_at": to_utc_iso_z(job.claim_expires_at),
            "completed_at": to_utc_iso_z(job.completed_at),
            "last_error": job.last_error,
            "created_at": to_utc_iso_z(job.created_at),
            "updated_at": to_utc_iso_z(job.updated_at),
        }

    def enqueue_picklist_print(
        self,
        order: Order,
        *,
        trigger_source: str,
        requested_by: Optional[str],
    ) -> PrintJob:
        if not order.picklist_path:
            raise ValidationError("Picklist PDF is not available for printing")

        existing_job = (
            self.db.query(PrintJob)
            .filter(
                PrintJob.order_id == order.id,
                PrintJob.document_type == PrintJobDocumentType.PICKLIST,
                PrintJob.status.in_((PrintJobStatus.PENDING, PrintJobStatus.CLAIMED)),
            )
            .first()
        )
        if existing_job:
            raise ConflictError(
                "A picklist print job is already pending for this order"
            )

        job = PrintJob(
            order_id=order.id,
            document_type=PrintJobDocumentType.PICKLIST,
            status=PrintJobStatus.PENDING,
            trigger_source=trigger_source,
            requested_by=requested_by,
            file_path=order.picklist_path,
        )
        self.db.add(job)
        self.db.flush()

        audit = AuditService(self.db)
        action = (
            "picklist_auto_print_queued"
            if trigger_source == PrintJobTriggerSource.AUTOMATIC
            else "picklist_manual_reprint_queued"
        )
        audit.log_order_action(
            order_id=order.id,
            action=action,
            user_id=requested_by,
            description=f"Queued picklist print job ({trigger_source})",
            audit_metadata={
                "print_job_id": job.id,
                "document_type": job.document_type,
                "trigger_source": job.trigger_source,
            },
        )
        return job

    def list_jobs(
        self,
        *,
        status: Optional[str] = None,
        document_type: str = PrintJobDocumentType.PICKLIST,
        limit: int = 25,
    ) -> list[PrintJob]:
        query = (
            self.db.query(PrintJob)
            .options(selectinload(PrintJob.order))
            .filter(PrintJob.document_type == document_type)
            .order_by(PrintJob.created_at.desc())
        )
        if status:
            query = query.filter(PrintJob.status == status)
        return query.limit(limit).all()

    def get_order_jobs(self, order_id: str) -> list[PrintJob]:
        return (
            self.db.query(PrintJob)
            .options(selectinload(PrintJob.order))
            .filter(
                PrintJob.order_id == order_id,
                PrintJob.document_type == PrintJobDocumentType.PICKLIST,
            )
            .order_by(PrintJob.created_at.desc())
            .all()
        )

    def release_expired_claims(self, *, claim_timeout_seconds: int) -> int:
        now = datetime.utcnow()
        expired_jobs = (
            self.db.query(PrintJob)
            .filter(
                PrintJob.status == PrintJobStatus.CLAIMED,
                PrintJob.claim_expires_at.isnot(None),
                PrintJob.claim_expires_at < now,
            )
            .all()
        )
        for job in expired_jobs:
            if job.document_type == PrintJobDocumentType.SHIPPING_LABEL:
                job.status = PrintJobStatus.FAILED
                job.claim_expires_at = None
                job.last_error = "Printer confirmation expired. Check the Brother output before requesting a reprint."
                continue
            job.status = PrintJobStatus.PENDING
            job.claimed_at = None
            job.claim_expires_at = None
            job.last_error = "Claim expired before completion"
        return len(expired_jobs)

    def claim_next_pending_job(
        self, *, claim_timeout_seconds: int, document_types: tuple[str, ...] = (PrintJobDocumentType.PICKLIST,)
    ) -> Optional[PrintJob]:
        self.release_expired_claims(claim_timeout_seconds=claim_timeout_seconds)

        job = (
            self.db.query(PrintJob)
            .options(selectinload(PrintJob.order))
            .filter(
                PrintJob.status == PrintJobStatus.PENDING,
                PrintJob.document_type.in_(document_types),
            )
            .order_by(PrintJob.created_at.asc())
            .with_for_update()
            .first()
        )
        if not job:
            return None

        now = datetime.utcnow()
        job.status = PrintJobStatus.CLAIMED
        job.claimed_at = now
        job.claim_expires_at = now + timedelta(seconds=claim_timeout_seconds)
        job.attempt_count = (job.attempt_count or 0) + 1
        job.last_error = None
        self.db.flush()
        return job

    def get_job(self, job_id: str) -> PrintJob:
        job = (
            self.db.query(PrintJob)
            .options(selectinload(PrintJob.order))
            .filter(PrintJob.id == job_id)
            .first()
        )
        if not job:
            raise NotFoundError("Print job not found")
        return job

    def mark_completed(self, job_id: str) -> PrintJob:
        job = self.get_job(job_id)
        if job.status != PrintJobStatus.CLAIMED:
            raise ValidationError("Only claimed print jobs can be completed")

        job.status = PrintJobStatus.COMPLETED
        job.completed_at = datetime.utcnow()
        job.claim_expires_at = None
        job.last_error = None

        audit = AuditService(self.db)
        audit.log_order_action(
            order_id=job.order_id,
            action="shipping_label_printed" if job.document_type == PrintJobDocumentType.SHIPPING_LABEL else "picklist_auto_printed",
            user_id=job.requested_by,
            description=f"{job.document_type} print job completed",
            audit_metadata={
                "print_job_id": job.id,
                "trigger_source": job.trigger_source,
                "attempt_count": job.attempt_count,
            },
        )
        self.db.flush()
        return job

    def mark_failed(self, job_id: str, *, error_message: str) -> PrintJob:
        job = self.get_job(job_id)
        if job.status != PrintJobStatus.CLAIMED:
            raise ValidationError("Only claimed print jobs can be failed")

        job.status = PrintJobStatus.FAILED
        job.claim_expires_at = None
        job.last_error = error_message[:4000]

        audit = AuditService(self.db)
        audit.log_order_action(
            order_id=job.order_id,
            action="shipping_label_print_failed" if job.document_type == PrintJobDocumentType.SHIPPING_LABEL else "picklist_auto_print_failed",
            user_id=job.requested_by,
            description=f"{job.document_type} print job failed",
            audit_metadata={
                "print_job_id": job.id,
                "trigger_source": job.trigger_source,
                "attempt_count": job.attempt_count,
                "error": job.last_error,
            },
        )
        self.db.flush()
        return job


def emit_print_job_available(job: PrintJob) -> None:
    from app.main import socketio

    socketio.emit(
        PRINT_JOB_AVAILABLE_EVENT,
        {
            "print_job_id": job.id,
            "order_id": job.order_id,
            "document_type": job.document_type,
            "trigger_source": job.trigger_source,
        },
        room=PRINT_JOB_ROOM,
    )


def emit_orders_update(message: str = "Print jobs updated") -> None:
    from app.main import socketio

    socketio.emit(
        "orders_update",
        {"message": message, "timestamp": to_utc_iso_z(datetime.utcnow())},
        room="orders",
    )
