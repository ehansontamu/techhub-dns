"""Resumable delivery proof stored in SharePoint; photo content is transient."""

import hashlib
import io
import re
from copy import deepcopy
from datetime import datetime
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session
from werkzeug.datastructures import FileStorage

from app.models.audit_log import AuditLog
from app.models.order import Order, OrderStatus
from app.schemas.order import SignatureData
from app.services.audit_service import AuditService
from app.services.order_service import OrderService
from app.services.sharepoint_service import get_sharepoint_service
from app.utils.exceptions import ConflictError, ExternalServiceError, NotFoundError, ValidationError
from app.utils.timezone import to_utc_iso_z

MAX_PHOTOS = 10
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTO_PIXELS = 24_000_000
MAX_REQUEST_BYTES = MAX_PHOTOS * MAX_PHOTO_BYTES + 1024 * 1024


def current_evidence(order: Order) -> dict | None:
    """Match proof to the current signature; reopened orders cannot reuse old proof."""
    if not order.signature_captured_at:
        return None
    signed_at = to_utc_iso_z(order.signature_captured_at)
    for record in reversed(order.delivery_evidence or []):
        if (
            record["signed_at"] == signed_at
            and record["signed_picklist_path"] == order.signed_picklist_path
            and record["bundle_path"] == order.bundle_path
        ):
            return record
    return None


def normalize_photo(upload: FileStorage) -> tuple[str, bytes]:
    """Validate actual image content, resize, and strip EXIF/GPS through re-encoding."""
    content = upload.read(MAX_PHOTO_BYTES + 1)
    if not content or len(content) > MAX_PHOTO_BYTES:
        raise ValidationError("Each photo must be nonempty and no larger than 10 MB")
    try:
        with Image.open(io.BytesIO(content)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValidationError("Photos must be JPEG, PNG, or WebP images")
            if source.width * source.height > MAX_PHOTO_PIXELS:
                raise ValidationError("Photos must be no larger than 24 megapixels")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.thumbnail((2048, 2048))
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            output = io.BytesIO()
            background.save(output, format="JPEG", quality=85)
            normalized = output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValidationError("Unable to read photo; choose a valid JPEG, PNG, or WebP image") from exc
    return hashlib.sha256(normalized).hexdigest(), normalized


class DeliveryEvidenceService:
    def __init__(self, db: Session):
        self.db = db
        self.orders = OrderService(db)

    def _order(self, order_id: str) -> Order:
        order = self.orders._resolve_order(order_id, lock=True)
        if order is None:
            raise NotFoundError("Order", order_id)
        return order

    @staticmethod
    def _require_in_delivery(order: Order):
        if order.status != OrderStatus.IN_DELIVERY.value:
            raise ValidationError("Order must be In Delivery to record delivery proof")

    def stage_signature(self, order_id: str, signature_data: SignatureData, actor: str) -> dict:
        order = self._order(order_id)
        self._require_in_delivery(order)
        existing = current_evidence(order)
        if existing and not existing.get("completed_at"):
            self.db.commit()
            return existing
        self.orders.assert_not_stale(order, signature_data.expected_updated_at)
        expected_updated_at = order.updated_at
        resolved_id = order.id
        # Release the row lock while generating PDFs and uploading to SharePoint.
        self.db.commit()
        signed_url, bundle_url, folder_url = self.orders.generate_bundled_documents(
            resolved_id, signature_data.model_dump(exclude={"expected_updated_at"})
        )
        # Generation assigns document paths in memory; reload before the concurrency check.
        self.db.expire_all()
        order = self._order(resolved_id)
        self._require_in_delivery(order)
        self.orders.assert_not_stale(order, expected_updated_at)
        # Existing MySQL DateTime columns store whole seconds.
        now = datetime.utcnow().replace(microsecond=0)
        record = {
            "id": str(uuid4()),
            "signed_at": to_utc_iso_z(now),
            "signed_by": actor,
            "signed_picklist_path": signed_url,
            "bundle_path": bundle_url,
            "partial_folder_url": folder_url,
            "photos": [],
            "skip_reason": None,
            "completed_at": None,
            "completed_by": None,
        }
        order.signature_captured_at = now
        order.signed_picklist_path = signed_url
        order.bundle_path = bundle_url
        order.delivery_evidence = [*(order.delivery_evidence or []), record]
        order.updated_at = now
        self._audit(order, actor, "Signature saved; delivery photos or a skip reason are required", record)
        self.db.commit()
        return record

    def _active_record(self, order: Order, record_id: str) -> dict:
        record = current_evidence(order)
        if not record or record["id"] != record_id:
            raise ConflictError("The delivery signature changed. Reopen the delivery proof step.")
        return deepcopy(record)

    @staticmethod
    def _save_record(order: Order, record: dict):
        order.delivery_evidence = [
            record if item["id"] == record["id"] else item
            for item in order.delivery_evidence or []
        ]
        order.updated_at = datetime.utcnow()

    def _audit(self, order: Order, actor: str, description: str, record: dict):
        self.db.add(AuditLog(
            order_id=order.id, changed_by=actor, from_status=order.status,
            to_status=order.status, reason=description, extra_metadata=record,
        ))
        AuditService(self.db).log_order_action(
            order.id, "delivery_evidence", user_id=actor,
            description=description, audit_metadata=record,
        )

    @staticmethod
    def _attach_photos_to_bundle(order: Order, record: dict, photo_contents: dict[str, bytes],
                                 sharepoint, subfolder: str, legacy_subfolder: str):
        """Rebuild from the original documents so a retry cannot duplicate photo pages."""
        order_number = order.inflow_order_id or order.id
        signed = sharepoint.download_file("signed", f"{order_number}_signed.pdf")
        checklist = sharepoint.download_file("qa", f"{order_number}_qa.pdf")
        if not signed or not checklist:
            raise ExternalServiceError("SharePoint", "download delivery documents")

        writer = PdfWriter()
        try:
            writer.append(io.BytesIO(signed))
            writer.append(io.BytesIO(checklist))
            for photo in record["photos"]:
                content = photo_contents.get(photo["sha256"])
                if content is None:
                    photo_folder = legacy_subfolder if photo["filename"].startswith("photo-") else subfolder
                    content = sharepoint.download_file(photo_folder, photo["filename"])
                if not content:
                    raise ExternalServiceError("SharePoint", "download delivery photo")
                with Image.open(io.BytesIO(content)) as image:
                    page_size = landscape(letter) if image.width > image.height else letter
                    page = io.BytesIO()
                    pdf = canvas.Canvas(page, pagesize=page_size)
                    margin = 36
                    scale = min(
                        (page_size[0] - 2 * margin) / image.width,
                        (page_size[1] - 2 * margin) / image.height,
                    )
                    width, height = image.width * scale, image.height * scale
                    pdf.drawImage(
                        ImageReader(image), (page_size[0] - width) / 2,
                        (page_size[1] - height) / 2, width=width, height=height,
                    )
                    pdf.save()
                    page.seek(0)
                    writer.append(PdfReader(page))
            output = io.BytesIO()
            writer.write(output)
            url = sharepoint.upload_file(output.getvalue(), subfolder, f"{order_number}_bundle.pdf")
        except ExternalServiceError:
            raise
        except Exception as exc:
            raise ExternalServiceError("SharePoint", "update delivery bundle") from exc
        if url != record["bundle_path"]:
            raise ExternalServiceError("SharePoint", "confirm delivery bundle update")

    def complete(self, order_id: str, record_id: str, uploads: list[FileStorage],
                 skip_reason: str | None, actor: str) -> tuple[Order, dict]:
        reason = (skip_reason or "").strip()
        if len(reason) > 1000:
            raise ValidationError("Photo skip reason must be 1000 characters or fewer")
        if uploads and reason:
            raise ValidationError("Choose photos or a skip reason, not both")
        if len(uploads) > MAX_PHOTOS:
            raise ValidationError("A delivery can have at most 10 photos")

        order = self._order(order_id)
        record = self._active_record(order, record_id)
        if record.get("completed_at") and order.status == OrderStatus.DELIVERED.value:
            self.db.commit()
            return order, record
        self._require_in_delivery(order)
        if reason and record["photos"]:
            raise ValidationError("Photos are already saved; complete delivery using those photos")
        if not uploads and not reason and not record["photos"]:
            raise ValidationError("At least one photo or a recorded skip reason is required")
        resolved_id = order.id
        subfolder, _family = self.orders._bundle_storage_location(order)
        safe_leg = re.sub(r"[^A-Za-z0-9._-]+", "_", order.inflow_order_id or order.id).strip("._-")
        family = self.orders._partial_order_family_number(order) or order.inflow_order_id or order.id
        safe_family = re.sub(r"[^A-Za-z0-9._-]+", "_", family).strip("._-") or order.id
        attempt = f"{safe_leg}_{record_id}" if family != order.inflow_order_id else record_id
        legacy_subfolder = f"delivery-photos/{safe_family}/{attempt}"
        self.db.commit()

        # Validate every selected file before the first external write. No disk archive.
        photos = dict(normalize_photo(upload) for upload in uploads)
        existing_hashes = {photo["sha256"] for photo in record["photos"]}
        if len(existing_hashes | photos.keys()) > MAX_PHOTOS:
            raise ValidationError("A delivery can have at most 10 photos")
        new_photos = {key: value for key, value in photos.items() if key not in existing_hashes}
        for checksum in new_photos:
            record["photos"].append({
                "url": record["bundle_path"],
                "filename": f"{safe_leg}_{record_id}_photo-{checksum}.jpg",
                "sha256": checksum,
                "uploaded_at": to_utc_iso_z(datetime.utcnow()),
                "uploaded_by": actor,
            })

        order = self._order(resolved_id)
        current_record = self._active_record(order, record_id)
        if current_record.get("completed_at") and order.status == OrderStatus.DELIVERED.value:
            self.db.commit()
            return order, current_record
        self._require_in_delivery(order)
        if not reason and not record["photos"]:
            raise ValidationError("At least one photo or a recorded skip reason is required")
        if record["photos"]:
            self._attach_photos_to_bundle(
                order, record, new_photos, get_sharepoint_service(), subfolder, legacy_subfolder
            )
            order = self._order(resolved_id)
            self._active_record(order, record_id)
            self._require_in_delivery(order)
        record["skip_reason"] = reason or None
        record["completed_at"] = to_utc_iso_z(datetime.utcnow())
        record["completed_by"] = actor
        self._save_record(order, record)
        description = f"Delivery photos skipped: {reason}" if reason else "Delivery photo requirement completed"
        self._audit(order, actor, description, record)
        # transition_status commits the proof, audit, and status in one transaction.
        order = self.orders.transition_status(
            resolved_id, OrderStatus.DELIVERED, changed_by=actor, reason=description
        )
        return order, record
