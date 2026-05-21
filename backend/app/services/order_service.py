import re
import json
import logging
import shutil
from copy import deepcopy
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from uuid import UUID
from typing import Union
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter

from app.models.order import Order, OrderStatus, ShippingWorkflowStatus
from app.models.order_status_history import OrderStatusHistory
from app.models.user import User
from app.utils.pdf_helpers import wrap_text, check_page_break, filter_picklines
from app.models.audit_log import AuditLog
from app.services.audit_service import AuditService

from app.utils.building_mapper import (
    get_building_abbreviation,
    extract_building_code_from_location,
)
from app.utils.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
    StatusTransitionError,
    FileOperationError,
)
from app.utils.timezone import to_utc_iso_z
from app.config import settings
from app.services.print_job_service import PrintJobService, emit_print_job_available
from app.services.order_splitting import OrderSplittingService
from app.services.system_setting_service import (
    SETTING_PICKLIST_AUTO_PRINT_ENABLED,
    SETTING_REQUIRE_ASSET_TAGS_BEFORE_PICKLIST,
    SETTING_REQUIRE_PARTIAL_PICKLIST_CONFIRMATION,
    SETTING_REQUIRE_SAME_USER_FOR_TAGGING_AND_PICKLIST,
    SystemSettingService,
)

logger = logging.getLogger(__name__)


class OrderService:
    VIDI_CUSTOM1_OVERRIDE = "TAMU - College of Veterinary Medicine"
    ZACH_CONTACT_OVERRIDE = "TAKODA POWELL"

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _as_dict(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _preserve_partial_split_inflow_snapshot(
        existing_order: Order,
        inflow_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merge a fresh InFlow snapshot without destroying locally split leg item sets."""
        merged = dict(inflow_data or {})
        if (
            existing_order.parent_order_id
            or existing_order.remainder_order_id
            or existing_order.has_remainder
        ):
            current_snapshot = dict(existing_order.inflow_data or {})
            # Preserve the split leg item set, but let fresh pick/pack/ship state
            # flow through so remainder legs can unblock once the remaining items
            # are actually picked.
            for field in ("lines", "subtotal", "total"):
                if field in current_snapshot:
                    merged[field] = deepcopy(current_snapshot.get(field))
        return merged

    def _requires_asset_tags(self, order: Order) -> bool:
        if not SystemSettingService.is_setting_enabled(
            SETTING_REQUIRE_ASSET_TAGS_BEFORE_PICKLIST
        ):
            return False

        if not order.inflow_data:
            return True

        from app.services.inflow_service import InflowService

        return InflowService().requires_asset_tags(order.inflow_data)

    @staticmethod
    def _apply_order_search(query, search: str):
        like = f"%{search}%"
        return query.filter(
            or_(
                Order.inflow_order_id.ilike(like),
                Order.inflow_sales_order_id.ilike(like),
                Order.recipient_name.ilike(like),
                Order.delivery_location.ilike(like),
                Order.po_number.ilike(like),
                Order.assigned_deliverer.ilike(like),
            )
        )

    def _apply_delivery_location_overrides(
        self, inflow_data: Dict[str, Any], delivery_location: str
    ) -> str:
        contact_name = inflow_data.get("contactName")
        shipping_addr_obj = self._as_dict(inflow_data.get("shippingAddress"))
        address1 = shipping_addr_obj.get("address1", "")
        address2 = shipping_addr_obj.get("address2", "")
        shipping_parts = [part for part in [address1, address2] if part]
        shipping_address = " ".join(shipping_parts) if shipping_parts else address1
        custom_fields = self._as_dict(inflow_data.get("customFields"))
        custom1 = custom_fields.get("custom1")

        if (
            isinstance(contact_name, str)
            and contact_name.strip().upper() == self.ZACH_CONTACT_OVERRIDE
            and delivery_location in {address1, address2, shipping_address, ""}
            and delivery_location != "ZACH"
        ):
            logger.info(
                "Overriding delivery_location to 'ZACH' based on contactName='%s'",
                contact_name,
            )
            return "ZACH"

        if (
            isinstance(custom1, str)
            and custom1.strip() == self.VIDI_CUSTOM1_OVERRIDE
            and delivery_location != "VIDI"
        ):
            logger.info(
                "Overriding delivery_location to 'VIDI' based on customFields.custom1='%s'",
                custom1,
            )
            return "VIDI"

        return delivery_location

    @staticmethod
    def _normalize_stale_timestamp(value: datetime) -> datetime:
        normalized = (
            value.astimezone(timezone.utc).replace(tzinfo=None)
            if value.tzinfo
            else value
        )
        return normalized.replace(microsecond=(normalized.microsecond // 1000) * 1000)

    def _resolve_actor_user_id(self, actor_identifier: Optional[str]) -> Optional[str]:
        if not actor_identifier:
            return None

        identifier = actor_identifier.strip()
        if not identifier or identifier.lower() == "system":
            return None

        user = (
            self.db.query(User)
            .filter(or_(User.id == identifier, User.email == identifier))
            .first()
        )
        return user.id if user else None

    def _record_status_history(
        self,
        *,
        order: Order,
        from_status: Optional[str],
        to_status: str,
        actor_identifier: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
        changed_at: Optional[datetime] = None,
    ) -> None:
        actor_user_id = self._resolve_actor_user_id(actor_identifier)
        history_entry = OrderStatusHistory(
            order_id=order.id,
            from_status=from_status,
            to_status=to_status,
            changed_at=changed_at or datetime.utcnow(),
            actor_user_id=actor_user_id,
            status_metadata=metadata,
        )
        self.db.add(history_entry)

    def _prep_steps_complete(self, order: Order) -> bool:
        tagging_complete = (not self._requires_asset_tags(order)) or bool(
            order.tagged_at
        )
        complete = bool(
            tagging_complete and order.picklist_generated_at and order.qa_completed_at
        )
        if not complete:
            steps = self._get_incomplete_steps(order)
            logger.info(f"Order {order.inflow_order_id} prep steps incomplete: {steps}")
        return complete

    def _get_incomplete_steps(self, order: Order) -> list[str]:
        """Return human-readable prep steps still required before Pre-Delivery."""
        missing_steps: list[str] = []

        if self._requires_asset_tags(order) and not order.tagged_at:
            missing_steps.append("asset_tagging")

        if not order.picklist_generated_at:
            missing_steps.append("picklist")

        if not order.qa_completed_at:
            missing_steps.append("qa")

        return missing_steps

    def _parent_remainder_has_unpicked_items(self, order: Order) -> bool:
        """Return True when a remainder parent leg is still waiting on item pickup."""
        pick_status = self._get_active_remainder_pick_status(
            order, warning_context="remainder leg"
        )
        if not pick_status:
            return False

        total_ordered = float(pick_status.get("total_ordered", 0) or 0)
        total_picked = float(pick_status.get("total_picked", 0) or 0)
        return total_ordered > 0 and total_picked < total_ordered

    def _parent_remainder_can_generate_picklist(self, order: Order) -> bool:
        """Return True when an active remainder leg has picked items that can be split off again."""
        pick_status = self._get_active_remainder_pick_status(
            order, warning_context="recursive remainder split"
        )
        if not pick_status:
            return False

        total_ordered = float(pick_status.get("total_ordered", 0) or 0)
        total_picked = float(pick_status.get("total_picked", 0) or 0)
        return total_ordered > 0 and 0 < total_picked < total_ordered

    def _get_active_remainder_pick_status(
        self, order: Order, *, warning_context: str
    ) -> Optional[Dict[str, Any]]:
        """Return the current pick status for an active remainder leg, if any."""
        if not getattr(order, "remainder_order_id", None) or getattr(
            order, "parent_order_id", None
        ):
            return None

        if not order.inflow_data:
            return None

        from app.services.inflow_service import InflowService
        from app.services.order_splitting import OrderSplittingService

        pick_status_source = OrderSplittingService(
            self.db
        ).build_parent_remainder_pick_status_source(order)
        if not pick_status_source:
            return None

        try:
            return InflowService().get_pick_status(pick_status_source)
        except Exception as exc:
            logger.warning(
                "Failed to compute pick status for %s %s: %s",
                warning_context,
                order.inflow_order_id,
                exc,
            )
            return None

    def _ensure_remainder_leg_ready(self, order: Order, action: str) -> None:
        """Block prep actions on remainder parent legs until their items are picked."""
        if not self._parent_remainder_has_unpicked_items(order):
            return

        raise ValidationError(
            "This remainder leg still has items waiting to be picked. "
            f"Finish picking the remaining items before you can {action}.",
            details={
                "order_id": order.inflow_order_id,
                "remainder_order_id": order.remainder_order_id,
            },
        )

    def _is_shipping_order(self, order: Order) -> bool:
        """Determine if an order is a shipping order (not local delivery)"""
        if not order.inflow_data:
            return False

        shipping_addr_obj = self._as_dict(order.inflow_data.get("shippingAddress"))
        city = (
            shipping_addr_obj.get("city", "").strip()
            if shipping_addr_obj.get("city")
            else ""
        )

        if city:
            city_upper = city.upper()
            return city_upper not in ("BRYAN", "COLLEGE STATION")

        return False  # Default to local delivery if no city specified

    def _storage_path(self, *parts: str) -> Path:
        return Path(settings.storage_root).joinpath(*parts)

    def _local_doc_path(self, category: str, filename: str) -> Path:
        """Return a local filesystem path for a document under configured storage."""
        base = Path(getattr(settings, 'local_document_storage', '/tmp/techhub-dns'))
        return base / category / filename

    def _resolve_order(
        self, order_id: str, lock: bool = False
    ) -> Optional[Order]:
        """Resolve an order by UUID, inflow_sales_order_id, or inflow_order_id (in that order).

        Returns the first match or None.
        """
        query = self.db.query(Order)
        if lock:
            query = query.with_for_update()

        order = query.filter(Order.id == order_id).first()
        if not order:
            order = query.filter(Order.inflow_sales_order_id == order_id).first()
        if not order:
            order = query.filter(Order.inflow_order_id == order_id).first()
        return order

    def _cleanup_order_documents(self, order: Order) -> None:
        """Delete local document files for an order after delivery completion."""
        paths = [
            order.picklist_path,
            order.qa_path,
            order.signed_picklist_path,
            order.order_details_path,
        ]
        for p_str in paths:
            if not p_str:
                continue
            if isinstance(p_str, str) and not p_str.startswith('http'):
                try:
                    p = Path(p_str)
                    if p.exists():
                        p.unlink()
                        logger.info(f"Cleaned up document: {p}")
                except Exception as e:
                    logger.warning(f"Failed to cleanup {p_str}: {e}")


    @staticmethod
    def _append_order_details_sent_marker(existing_remarks: Any) -> Optional[str]:
        marker = "Order Details Sent"
        normalized = str(existing_remarks or "")
        if marker in normalized:
            return None
        if not normalized.strip():
            return marker
        return f"{normalized.rstrip()}\n\n{marker}"

    def _mark_order_details_sent(
        self, order: Order, generated_by: Optional[str] = None
    ) -> bool:
        inflow_data = dict(order.inflow_data or {})
        updated_remarks = self._append_order_details_sent_marker(
            inflow_data.get("orderRemarks")
        )
        if updated_remarks is None:
            return True

        if not order.inflow_sales_order_id:
            logger.warning(
                "Order Details email sent for %s but order has no inflow_sales_order_id; skipping remarks update",
                order.inflow_order_id,
            )
            return False

        from app.services.inflow_service import InflowService

        try:
            inflow_service = InflowService()
            inflow_service.update_order_remarks_sync(
                order.inflow_sales_order_id, updated_remarks
            )
        except Exception as exc:
            logger.error(
                "Failed to update inFlow orderRemarks for %s after Order Details email: %s",
                order.inflow_order_id,
                exc,
            )
            return False

        inflow_data["orderRemarks"] = updated_remarks
        order.inflow_data = inflow_data
        order.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(order)

        audit_service = AuditService(self.db)
        audit_service.log_order_action(
            order_id=str(order.id),
            action="order_details_sent_marked",
            user_id=generated_by or "system",
            description="Added 'Order Details Sent' to inFlow order remarks",
            audit_metadata={"order_remarks": updated_remarks},
        )
        return True

    def send_order_details_email(
        self,
        order_id: Union[UUID, str],
        generated_by: Optional[str] = None,
    ) -> bool:
        order_id_str = str(order_id)
        order = self._resolve_order(order_id_str, lock=True)
        if not order:
            raise NotFoundError("Order", str(order_id))

        self._ensure_remainder_leg_ready(order, "send the order details email")
        return self._send_order_details_email(order, generated_by)

    def mark_asset_tagged(
        self,
        order_id: Union[UUID, str],
        tag_ids: List[str],
        technician: Optional[str] = None,
        expected_updated_at: Optional[datetime] = None,
    ) -> Order:
        order_id_str = str(order_id)
        order = self._resolve_order(order_id_str, lock=True)
        if not order:
            raise NotFoundError("Order", str(order_id))

        self.assert_not_stale(order, expected_updated_at)
        self._ensure_remainder_leg_ready(order, "tag assets")

        tag_data = dict(order.tag_data or {})
        tag_data["tag_ids"] = tag_ids

        order.tagged_at = datetime.utcnow()
        order.tagged_by = technician
        order.tag_data = tag_data
        order.updated_at = datetime.utcnow()

        self.db.commit()
        self.db.refresh(order)

        # Audit logging for asset tagging
        audit_service = AuditService(self.db)
        audit_service.log_order_action(
            order_id=str(order_id),
            action="asset_tagged",
            user_id=technician or "unknown",
            description=f"Order tagged with {len(tag_ids)} asset tags",
            audit_metadata={"tag_ids": tag_ids, "tagged_by": technician},
        )

        # If this order requires asset tags and is a partial pick,
        # create the partial leg now so picklist generation can proceed
        # without requiring a separate confirmation step.
        if self._requires_asset_tags(order):
            from app.services.inflow_service import InflowService
            pick_status = InflowService().get_pick_status(order.inflow_data)
            is_partial = bool(
                pick_status
                and pick_status.get("total_ordered", 0) > 0
                and pick_status.get("total_picked", 0) > 0
                and pick_status.get("total_picked", 0) < pick_status.get("total_ordered", 0)
            )
            if is_partial:
                from app.services.order_splitting import OrderSplittingService
                splitting_service = OrderSplittingService(self.db)
                splitting_service.create_partial_picklist_leg(order, technician)

        return order

    def generate_picklist(
        self,
        order_id: Union[UUID, str],
        generated_by: Optional[str] = None,
        generated_by_display: Optional[str] = None,
        expected_updated_at: Optional[datetime] = None,
        create_partial_leg: bool = False,
    ) -> Order:
        order_id_str = str(order_id).strip()
        order = self._resolve_order(order_id_str, lock=True)
        if not order:
            raise NotFoundError("Order", str(order_id))

        self.assert_not_stale(order, expected_updated_at)
        if not self._parent_remainder_can_generate_picklist(order):
            self._ensure_remainder_leg_ready(order, "generate the picklist")

        if not order.inflow_data:
            raise ValidationError("Order must have inFlow data to generate picklist")

        from app.services.inflow_service import InflowService

        pick_status = None
        if order.inflow_data:
            try:
                pick_status = InflowService().get_pick_status(order.inflow_data)
            except Exception as exc:
                logger.warning(
                    "Failed to compute pick status for order %s before picklist generation: %s",
                    order.inflow_order_id,
                    exc,
                )

        is_partial_order = bool(
            pick_status
            and pick_status.get("total_ordered", 0) > 0
            and pick_status.get("total_picked", 0) > 0
            and pick_status.get("total_picked", 0) < pick_status.get("total_ordered", 0)
        )

        if is_partial_order and not order.parent_order_id:
            # For a partially picked parent, create or resolve the child leg first.
            # The child leg is the one that should receive the generated picklist,
            # Order Details email, and downstream QA/status timestamps.
            if not (
                create_partial_leg
                or not SystemSettingService.is_setting_enabled(
                    SETTING_REQUIRE_PARTIAL_PICKLIST_CONFIRMATION
                )
            ):
                raise ValidationError(
                    "This order is partially picked. Confirm creation of the partial leg before generating the picklist.")
            else:
                split_order = OrderSplittingService(self.db).create_partial_picklist_leg(
                    order, generated_by
                )
                if split_order is not None:
                    order = split_order

        # Enforce that the user generating the picklist is the same user who tagged the assets,
        # unless one of them is missing (legacy data).
        # tagged_by is stored as email; generated_by is now also email, but we also check
        # generated_by_display as a fallback for callers that pass a display name.
        if (
            SystemSettingService.is_setting_enabled(
                SETTING_REQUIRE_SAME_USER_FOR_TAGGING_AND_PICKLIST
            )
            and order.tagged_by
            and generated_by
            and order.tagged_by != generated_by
        ):
            same_user = False
            if generated_by_display:
                if order.tagged_by == generated_by_display:
                    same_user = True
                # Strip email domain and compare local parts
                def _local_part(val: str) -> str:
                    return val.strip().lower().split('@')[0] if '@' in val else val.strip().lower()
                if _local_part(order.tagged_by) == _local_part(generated_by_display):
                    same_user = True
            if not same_user:
                raise ValidationError(
                    f"Asset tagging and picklist generation must be performed by the same user. "
                    f"Tagged by: {order.tagged_by}, current user: {generated_by_display or generated_by}"
                )

        is_first_picklist = order.picklist_generated_at is None

        filename = f"{order.inflow_order_id or order.id}.pdf"
        document_inflow_data = order.inflow_data
        if getattr(order, "remainder_order_id", None) and not getattr(order, "parent_order_id", None):
            remainder_view = OrderSplittingService(self.db).build_parent_remainder_picklist_view(order)
            if remainder_view is not None:
                document_inflow_data = remainder_view

        # Generate picklist to a temporary file, upload immediately, store SharePoint URL
        import tempfile
        from pathlib import Path
        
        temp_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        temp_path = temp_pdf.name
        temp_pdf.close()
        
        try:
            # Generate the actual picklist PDF from inFlow data using PicklistService
            from app.services.picklist_service import PicklistService
            picklist_svc = PicklistService()
            picklist_svc.generate_picklist_pdf(document_inflow_data, temp_path)

            # Save picklist to local storage
            local_dir = self._local_doc_path("picklists", filename).parent
            local_dir.mkdir(parents=True, exist_ok=True)
            local_path = self._local_doc_path("picklists", filename)
            shutil.copy2(temp_path, local_path)
            logger.info(f"Picklist saved locally: {local_path}")

            # Upload to SharePoint immediately; this is required.
            sp_url = None
            try:
                from app.services.sharepoint_service import get_sharepoint_service

                sp_service = get_sharepoint_service()
                if not sp_service.is_enabled:
                    raise RuntimeError("SharePoint storage is not enabled")
                sp_url = sp_service.upload_pdf(temp_path, "picklists", filename)
                logger.info(f"Picklist uploaded to SharePoint: {sp_url}")
            except Exception as e:
                logger.error("SharePoint upload failed for picklist: %s", e)
                raise

            order.picklist_generated_at = datetime.utcnow()
            order.picklist_generated_by = generated_by_display or generated_by
            order.picklist_path = sp_url or str(local_path)
            order.updated_at = datetime.utcnow()
        finally:
            # Clean up temporary file
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Failed to delete temp picklist {temp_path}: {e}")

        queued_print_job = None
        if is_first_picklist and SystemSettingService.get_setting(
            self.db, SETTING_PICKLIST_AUTO_PRINT_ENABLED
        ).lower() in {"true", "1", "yes", "on"}:
            queued_print_job = PrintJobService(self.db).enqueue_picklist_print(
                order,
                trigger_source="automatic",
                requested_by=generated_by_display or generated_by,
            )

        self.db.commit()
        self.db.refresh(order)
        if queued_print_job is not None:
            emit_print_job_available(queued_print_job)

        # Audit logging for picklist generation
        display_name = generated_by_display or generated_by
        audit_service = AuditService(self.db)
        audit_service.log_order_action(
            order_id=str(order_id),
            action="picklist_generated",
            user_id=display_name,
            description=f"Picklist PDF generated",
            audit_metadata={
                "filename": filename,
                "generated_by": display_name,
                "sharepoint_url": sp_url,
            },
        )

        # Send Order Details email to recipient (after picklist, before QA)
        self._send_order_details_email(order, generated_by)

        # Transition order to QA status (awaiting QA checklist)
        if order.status == OrderStatus.PICKED.value:
            old_status = order.status
            order.status = OrderStatus.QA.value
            order.updated_at = datetime.utcnow()

            # Create AuditLog entry for timeline display
            audit_log = AuditLog(
                order_id=order.id,
                changed_by=generated_by or "system",
                from_status=old_status,
                to_status=OrderStatus.QA.value,
                reason="Picklist generated - moved to QA queue",
                timestamp=datetime.utcnow(),
            )
            self.db.add(audit_log)
            self._record_status_history(
                order=order,
                from_status=old_status,
                to_status=OrderStatus.QA.value,
                actor_identifier=generated_by,
                metadata={"reason": "Picklist generated - moved to QA queue"},
            )
            self.db.commit()
            self.db.refresh(order)

            # Also log to system audit log for full traceability
            audit_service.log_order_action(
                order_id=str(order_id),
                action="status_changed",
                user_id=generated_by or "system",
                description="Order moved to QA queue after picklist generation",
                audit_metadata={
                    "from_status": OrderStatus.PICKED.value,
                    "to_status": OrderStatus.QA.value,
                },
            )

        return order

    def submit_qa(
        self,
        order_id: Union[UUID, str],
        qa_data: Dict[str, Any],
        technician: Optional[str] = None,
        expected_updated_at: Optional[datetime] = None,
    ) -> Order:
        order_id_str = str(order_id)
        order = (
            self.db.query(Order)
            .filter(Order.id == order_id_str)
            .with_for_update()
            .first()
        )
        if not order:
            raise NotFoundError("Order", str(order_id))

        self.assert_not_stale(order, expected_updated_at)
        self._ensure_remainder_leg_ready(order, "complete QA")

        if not order.picklist_generated_at:
            raise ValidationError(
                "Picklist must be generated before QA can be completed"
            )

        # Inject authenticated technician into QA data if provided
        # This ensures consistency between the audit log and the stored form data
        if technician:
            qa_data["technician"] = technician

        # Validate QA data format - must use detailed shipping QA format
        required_fields = [
            "orderNumber",
            "technician",
            "qaSignature",
            "method",
            "verifyAssetTagSerialMatch",
            "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved",
            "verifyPackagedProperly",
            "verifyPackingSlipSerialsMatch",
            "verifyBoxesLabeledCorrectly",
        ]

        missing_fields = [field for field in required_fields if field not in qa_data]
        if missing_fields:
            raise ValidationError(
                f"QA data missing required fields for detailed format: {', '.join(missing_fields)}",
                details={"missing_fields": missing_fields},
            )

        # Validate method is either "Delivery" or "Shipping"
        if qa_data.get("method") not in ["Delivery", "Shipping"]:
            raise ValidationError(
                "QA method must be either 'Delivery' or 'Shipping'",
                field="method",
                details={"provided_method": qa_data.get("method")},
            )

        # Validate boolean fields are boolean
        boolean_fields = [
            "verifyAssetTagSerialMatch",
            "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved",
            "verifyPackagedProperly",
            "verifyPackingSlipSerialsMatch",
            "verifyBoxesLabeledCorrectly",
        ]
        for field in boolean_fields:
            if not isinstance(qa_data.get(field), bool):
                raise ValidationError(
                    f"QA field '{field}' must be a boolean value",
                    field=field,
                    details={"field_type": type(qa_data.get(field)).__name__},
                )

        qa_filename = f"{order.inflow_order_id or order.id}.json"
        qa_payload = {
            "order_id": str(order.id),
            "inflow_order_id": order.inflow_order_id,
            "submitted_at": to_utc_iso_z(datetime.utcnow()),
            "submitted_by": technician,
            "responses": qa_data,
        }

        # Upload QA JSON directly to SharePoint (source of truth)
        try:
            # Save QA JSON to local storage
            local_qadir = self._local_doc_path("qa", qa_filename).parent
            local_qadir.mkdir(parents=True, exist_ok=True)
            local_qapath = self._local_doc_path("qa", qa_filename)
            with open(local_qapath, "w") as fp:
                json.dump(qa_payload, fp)
            logger.info(f"QA data saved locally: {local_qapath}")
            qa_path = str(local_qapath)
        except Exception as e:
            logger.error(f"Failed to save QA data locally: {e}")
            raise

        # Upload QA JSON to SharePoint (source of truth) — synchronous
        try:
            from app.services.sharepoint_service import get_sharepoint_service
            sp_service = get_sharepoint_service()
            if not sp_service.is_enabled:
                raise RuntimeError("SharePoint storage is not enabled")
            with open(local_qapath, "rb") as fp:
                qa_bytes = fp.read()
            sp_url = sp_service.upload_file(qa_bytes, "qa", qa_filename)
            logger.info(f"QA data uploaded to SharePoint: {sp_url}")
            order.qa_path = sp_url  # Source of truth
        except Exception as e:
            logger.error(f"SharePoint upload failed for QA: {e}")
            raise  # Fail fast

        # Update order object (BUT DO NOT COMMIT YET to keep transition atomic)
        order.qa_completed_at = datetime.utcnow()
        order.qa_completed_by = technician
        order.qa_data = qa_data
        # order.qa_path already set to SharePoint sp_url above
        order.qa_method = qa_data.get("method")  # "Delivery" or "Shipping"
        order.updated_at = datetime.utcnow()

        # Audit logging for QA completion
        audit_service = AuditService(self.db)
        audit_service.log_order_action(
            order_id=str(order_id),
            action="qa_completed",
            user_id=technician or "unknown",
            description=f"QA completed using {qa_data.get('method', 'unknown')} method",
            audit_metadata={
                "qa_method": qa_data.get("method"),
                "completed_by": technician,
                "sharepoint_url": sp_url,
            },
        )

        # Automatically transition status based on QA method
        target_status = None
        method = qa_data.get("method")

        logger.info(f"Checking for auto-transition. Method: '{method}'")

        if method == "Delivery":
            target_status = OrderStatus.PRE_DELIVERY
        elif method == "Shipping":
            target_status = OrderStatus.SHIPPING

        if target_status:
            logger.info(
                f"Triggering auto-transition for order {order.inflow_order_id} to {target_status.value}"
            )
            try:
                # We use transition_status to ensure valid transitions and audit logging.
                # It will handle the final commit.
                return self.transition_status(
                    order_id=order.id,
                    new_status=target_status,
                    changed_by=technician,
                    reason=f"QA passed via {method} method",
                )
            except Exception as e:
                logger.error(
                    f"Auto-transition failed for order {order.inflow_order_id}: {e}"
                )
                # We do NOT commit the QA data if transition fails to maintain atomicity
                # provided this whole method is wrapped in a transaction (which it is by default in Flask context).
                # But to be explicit and safe:
                self.db.rollback()
                raise

        # If no auto-transition (e.g. invalid method), commit QA data now
        self.db.commit()
        self.db.refresh(order)
        return order

    def get_orders(
        self,
        status: Optional[OrderStatus] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[List[Order], int]:
        """Get orders with filters and pagination"""
        query = self.db.query(Order)

        if status:
            query = query.filter(Order.status == status.value)

        if search:
            query = self._apply_order_search(query, search.strip())

        total = query.count()
        orders = (
            query.order_by(
                Order.updated_at.desc(),
                Order.created_at.desc(),
                Order.id.desc(),
            )
            .offset(skip)
            .limit(limit)
            .all()
        )

        return orders, total

    def get_order_by_id(self, order_id: Union[UUID, str]) -> Optional[Order]:
        """Get a single order by ID or order number."""
        order_id_str = str(order_id).strip()
        # Try UUID first for backward compatibility
        order = self.db.query(Order).filter(Order.id == order_id_str).first()
        if order is not None:
            return order
        # Fall back to order number
        return self.db.query(Order).filter(Order.inflow_order_id == order_id_str).first()

    def get_order_detail(self, order_id: Union[UUID, str]) -> Optional[Order]:
        """Get order with related data (audit logs, notifications) by ID or order number."""
        order_id_str = str(order_id).strip()
        # Try UUID first for backward compatibility
        order = (
            self.db.query(Order)
            .options(
                selectinload(Order.audit_logs),
                selectinload(Order.print_jobs),
            )
            .filter(Order.id == order_id_str)
            .first()
        )
        if order is not None:
            return order
        # Fall back to order number
        return (
            self.db.query(Order)
            .options(
                selectinload(Order.audit_logs),
                selectinload(Order.print_jobs),
            )
            .filter(Order.inflow_order_id == order_id_str)
            .first()
        )

    def assert_not_stale(
        self,
        order: Order,
        expected_updated_at: Optional[datetime],
    ) -> None:
        if expected_updated_at is None or order.updated_at is None:
            return

        expected = self._normalize_stale_timestamp(expected_updated_at)
        actual = self._normalize_stale_timestamp(order.updated_at)

        if actual == expected:
            return

        logger.warning(
            "Stale order update blocked: order_id=%s inflow_order_id=%s expected_updated_at=%s actual_updated_at=%s",
            order.id,
            order.inflow_order_id,
            to_utc_iso_z(expected),
            to_utc_iso_z(actual),
        )
        raise ConflictError(
            "Order has been updated by another user. Refresh and try again.",
            details={
                "order_id": str(order.id),
                "expected_updated_at": to_utc_iso_z(expected),
                "actual_updated_at": to_utc_iso_z(actual),
            },
        )

    def transition_status(
        self,
        order_id: Union[UUID, str],
        new_status: OrderStatus,
        changed_by: Optional[str] = None,
        reason: Optional[str] = None,
        expected_updated_at: Optional[datetime] = None,
    ) -> Order:
        """Transition order status with validation and audit logging"""
        order_id_str = str(order_id)
        # Try UUID first, fall back to inflow_order_id (TH-number)
        order = (
            self.db.query(Order)
            .filter(Order.id == order_id_str)
            .with_for_update()
            .first()
        )
        if order is None:
            order = (
                self.db.query(Order)
                .filter(Order.inflow_order_id == order_id_str)
                .with_for_update()
                .first()
            )
        if not order:
            raise NotFoundError("Order", str(order_id))

        self.assert_not_stale(order, expected_updated_at)

        old_status = order.status
        qa_method = order.qa_method.strip().lower() if order.qa_method else None

        # Validate transition
        if not self._is_valid_transition(old_status, new_status.value):
            raise StatusTransitionError(old_status, new_status.value)

        if new_status == OrderStatus.PRE_DELIVERY:
            # Log exact state for debugging
            logger.info(
                f"Checking PRE_DELIVERY requirements for {order.inflow_order_id}: tagged={bool(order.tagged_at)}, picklist={bool(order.picklist_generated_at)}, qa={bool(order.qa_completed_at)}"
            )

            if not self._prep_steps_complete(order):
                raise ValidationError(
                    "Asset tagging, picklist, and QA must be completed before Pre-Delivery",
                    details={
                        "missing_steps": self._get_incomplete_steps(order),
                        "order_id": order.inflow_order_id,
                    },
                )

        if new_status in (OrderStatus.IN_DELIVERY, OrderStatus.SHIPPING):
            if not qa_method:
                raise ValidationError(
                    "QA method must be set before routing an order to Delivery or Shipping",
                    field="qa_method",
                )
            if new_status == OrderStatus.IN_DELIVERY and qa_method != "delivery":
                raise ValidationError(
                    "Order QA method must be Delivery to transition to In Delivery",
                    field="qa_method",
                    details={
                        "qa_method": order.qa_method,
                        "requested_status": new_status.value,
                    },
                )
            if new_status == OrderStatus.SHIPPING and qa_method != "shipping":
                raise ValidationError(
                    "Order QA method must be Shipping to transition to Shipping",
                    field="qa_method",
                    details={
                        "qa_method": order.qa_method,
                        "requested_status": new_status.value,
                    },
                )

        if (
            new_status == OrderStatus.IN_DELIVERY
            and old_status == OrderStatus.PRE_DELIVERY
        ):
            if not order.delivery_run_id:
                raise ValidationError(
                    "Order must be assigned to a delivery run before transitioning to In-Delivery"
                )
            if self._is_shipping_order(order):
                raise ValidationError(
                    "Shipping orders cannot be transitioned to In-Delivery"
                )

        if new_status == OrderStatus.SHIPPING:
            if not self._is_shipping_order(order):
                raise ValidationError(
                    "Only shipping orders (outside Bryan/College Station) can be transitioned to Shipping"
                )

        # Require reason for Issue status
        if new_status == OrderStatus.ISSUE and not reason:
            raise ValidationError("Reason is required when flagging an issue")

        # Update order status
        order.status = new_status.value
        order.updated_at = datetime.utcnow()

        if new_status == OrderStatus.ISSUE:
            order.issue_reason = reason
            # Detach from any delivery run when raising an issue
            order.delivery_run_id = None

        # Create audit log
        audit_log = AuditLog(
            order_id=order.id,
            changed_by=changed_by,
            from_status=old_status,
            to_status=new_status.value,
            reason=reason,
            timestamp=datetime.utcnow(),
        )
        self.db.add(audit_log)
        self._record_status_history(
            order=order,
            from_status=old_status,
            to_status=new_status.value,
            actor_identifier=changed_by,
            metadata={"reason": reason} if reason else None,
        )

        # Note: Teams notification should be sent via BackgroundTasks in the route handler
        # This service method doesn't send notifications directly to avoid blocking

        self.db.commit()
        self.db.refresh(order)

        return order

    def _rollback_targets_for_status(self, current_status: str) -> set[str]:
        """Return the statuses an order can be rolled back to from the current status."""
        # Rollback is only permitted from ISSUE status (quarantine-first workflow)
        if current_status == OrderStatus.ISSUE.value:
            return {
                OrderStatus.PICKED.value,
                OrderStatus.QA.value,
                OrderStatus.PRE_DELIVERY.value,
            }
        return set()

    def rollback_status(
        self,
        order_id: Union[UUID, str],
        target_status: OrderStatus,
        changed_by: Optional[str] = None,
        reason: Optional[str] = None,
        expected_updated_at: Optional[datetime] = None,
    ) -> Order:
        """Roll an order back to an earlier workflow status and clear downstream state."""
        order_id_str = str(order_id)
        order = (
            self.db.query(Order)
            .filter(Order.id == order_id_str)
            .with_for_update()
            .first()
        )
        if order is None:
            order = (
                self.db.query(Order)
                .filter(Order.inflow_order_id == order_id_str)
                .with_for_update()
                .first()
            )
        if not order:
            raise NotFoundError("Order", str(order_id))

        self.assert_not_stale(order, expected_updated_at)

        current_status = order.status
        allowed_targets = self._rollback_targets_for_status(current_status)
        if target_status.value not in allowed_targets:
            raise ValidationError(
                f"Cannot rollback order from {current_status} to {target_status.value}",
                details={
                    "order_id": order.inflow_order_id,
                    "current_status": current_status,
                    "target_status": target_status.value,
                    "allowed_targets": sorted(allowed_targets),
                },
            )

        if current_status == target_status.value:
            raise ValidationError("Order is already in the requested status")

        cleared_fields: Dict[str, Any] = {}

        def clear(field_name: str) -> None:
            cleared_fields[field_name] = getattr(order, field_name)
            setattr(order, field_name, None)

        clear("issue_reason")
        clear("delivery_run_id")
        clear("delivery_sequence")
        clear("shipping_workflow_status_updated_at")
        clear("shipping_workflow_status_updated_by")
        clear("shipped_to_carrier_at")
        clear("shipped_to_carrier_by")
        clear("carrier_name")
        clear("tracking_number")
        clear("signature_captured_at")
        clear("signed_picklist_path")
        order.shipping_workflow_status = ShippingWorkflowStatus.WORK_AREA.value

        if target_status in (OrderStatus.QA, OrderStatus.PICKED):
            clear("assigned_deliverer")

        if target_status == OrderStatus.QA:
            clear("qa_completed_at")
            clear("qa_completed_by")
            clear("qa_data")
            clear("qa_path")
            clear("qa_method")

        if target_status == OrderStatus.PICKED:
            clear("tagged_at")
            clear("tagged_by")
            clear("tag_data")
            clear("picklist_generated_at")
            clear("picklist_generated_by")
            clear("picklist_path")
            clear("order_details_path")
            clear("order_details_generated_at")
            clear("qa_completed_at")
            clear("qa_completed_by")
            clear("qa_data")
            clear("qa_path")
            clear("qa_method")

        order.status = target_status.value
        order.updated_at = datetime.utcnow()

        audit_log = AuditLog(
            order_id=order.id,
            changed_by=changed_by,
            from_status=current_status,
            to_status=target_status.value,
            reason=reason,
            timestamp=datetime.utcnow(),
        )
        self.db.add(audit_log)
        self._record_status_history(
            order=order,
            from_status=current_status,
            to_status=target_status.value,
            actor_identifier=changed_by,
            metadata={
                "reason": reason,
                "rollback": True,
                "cleared_fields": sorted(cleared_fields.keys()),
            },
        )

        self.db.commit()
        self.db.refresh(order)

        return order

    def _is_valid_transition(self, from_status: str, to_status: str) -> bool:
        """Validate status transition"""
        valid_transitions = {
            OrderStatus.PICKED.value: [
                OrderStatus.QA.value,
                OrderStatus.PRE_DELIVERY.value,
                OrderStatus.SHIPPING.value,
                OrderStatus.ISSUE.value,
            ],
            OrderStatus.QA.value: [
                OrderStatus.PRE_DELIVERY.value,
                OrderStatus.SHIPPING.value,
                OrderStatus.ISSUE.value,
            ],
            OrderStatus.PRE_DELIVERY.value: [
                OrderStatus.IN_DELIVERY.value,
                OrderStatus.SHIPPING.value,
                OrderStatus.ISSUE.value,
            ],
            OrderStatus.IN_DELIVERY.value: [
                OrderStatus.DELIVERED.value,
                OrderStatus.ISSUE.value,
            ],
            OrderStatus.SHIPPING.value: [
                OrderStatus.DELIVERED.value,
                OrderStatus.ISSUE.value,
            ],
            OrderStatus.ISSUE.value: [
                OrderStatus.PICKED.value,
                OrderStatus.QA.value,
                OrderStatus.PRE_DELIVERY.value,
            ],
            OrderStatus.DELIVERED.value: [],  # Terminal state
        }

        return to_status in valid_transitions.get(from_status, [])

    def bulk_transition(
        self,
        order_ids: List[Union[UUID, str]],
        new_status: OrderStatus,
        changed_by: Optional[str] = None,
        reason: Optional[str] = None,
        expected_updated_at: Optional[datetime] = None,
    ) -> List[Order]:
        """Bulk transition multiple orders with locking and logging."""
        # Lock all orders up front to prevent concurrent modifications
        order_id_strs = [str(oid) for oid in order_ids]
        orders = (
            self.db.query(Order)
            .filter(Order.id.in_(order_id_strs))
            .with_for_update()
            .all()
        )

        # Validate we found all requested orders
        if len(orders) != len(order_id_strs):
            found_ids = {str(o.id) for o in orders}
            missing = [oid for oid in order_id_strs if oid not in found_ids]
            raise NotFoundError("Order(s)", ", ".join(missing[:5]))

        # Validate staleness for all orders before making any changes
        if expected_updated_at is not None:
            for order in orders:
                self.assert_not_stale(order, expected_updated_at)

        # Transition each order directly (already locked above).
        # transition_status() will re-acquire FOR UPDATE on the same row,
        # which is a no-op within the same transaction.
        successful_orders = []
        for order in orders:
            try:
                transitioned = self.transition_status(order.id, new_status, changed_by, reason)
                successful_orders.append(transitioned)
            except Exception as e:
                logger.warning("Failed to transition order %s: %s", order.id, e)
                continue

        return successful_orders

    def _extract_delivery_location_from_remarks(
        self, order_remarks: str
    ) -> Optional[str]:
        """
        Extract alternative delivery location from order remarks.
        Looks for patterns like "deliver to [location]" or "delivery to [location]"

        Example: "deliver to LAAH 424" -> "LAAH 424"
        """
        if not order_remarks:
            return None

        # Normalize the remarks (lowercase for case-insensitive matching)
        remarks_lower = order_remarks.lower()

        # Patterns to match: "deliver to", "delivery to", "deliver at", etc.
        patterns = [
            r"deliver\s+to\s+([^\r\n,]+)",  # "deliver to LAAH 424",
            r"delivery\s+to\s+([^\r\n,]+)",  # "delivery to LAAH 424",
            r"deliver\s+at\s+([^\r\n,]+)",  # "deliver at LAAH 424",
            # Auto-discovered patterns (from pattern analysis):
            r"deliver\s+to\s+([^\r\n,;]+?)(?:\s*[-–—]|\s*$|\r|\n|,|;|$)",  # "deliver to" (found 8 times)
            r"need\s+to\s+([^\r\n,;]+?)(?:\s*[-–—]|\s*$|\r|\n|,|;|$)",  # "need to" (found 6 times)
            r"located\s+at\s+([^\r\n,;]+?)(?:\s*[-–—]|\s*$|\r|\n|,|;|$)",  # "located at" (found 6 times)
        ]

        for pattern in patterns:
            match = re.search(pattern, remarks_lower, re.IGNORECASE)
            if match:
                location = match.group(1).strip()
                # Remove trailing punctuation and common words
                location = re.sub(r"[.,;:]+$", "", location)
                if location:
                    return location

        return None

    def _extract_order_basic_data(self, inflow_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract basic order data including order number, remarks, and shipping address."""
        order_number = inflow_data.get("orderNumber")
        if not order_number:
            raise ValidationError("Order number is required", field="orderNumber")

        order_remarks = inflow_data.get("orderRemarks", "")
        shipping_addr_obj = self._as_dict(inflow_data.get("shippingAddress"))
        address1 = shipping_addr_obj.get("address1", "")
        address2 = shipping_addr_obj.get("address2", "")

        shipping_address_parts = [part for part in [address1, address2] if part]
        shipping_address = (
            " ".join(shipping_address_parts) if shipping_address_parts else address1
        )

        return {
            "order_number": order_number,
            "order_remarks": order_remarks,
            "shipping_addr_obj": shipping_addr_obj,
            "address1": address1,
            "address2": address2,
            "shipping_address": shipping_address,
        }

    def _determine_city_and_delivery_type(
        self,
        order_number: str,
        shipping_addr_obj: Dict[str, Any],
        shipping_address: str,
    ) -> Dict[str, Any]:
        """Determine if this is a local delivery based on city detection."""
        city = (
            shipping_addr_obj.get("city", "").strip()
            if shipping_addr_obj.get("city")
            else ""
        )

        # If city is missing, try to detect it from the full address string for common non-local locations
        if not city and shipping_address:
            if "HOUSTON" in shipping_address.upper():
                city = "Houston"
                logger.info(
                    f"City not specified but 'Houston' found in address for order {order_number}. inferred_city='Houston'"
                )

        is_local_delivery = False

        if city:
            city_upper = city.upper()
            is_local_delivery = city_upper in ("BRYAN", "COLLEGE STATION")
            if not is_local_delivery:
                logger.info(
                    f"Order {order_number} is outside Bryan/College Station (city: '{city}'). This will be processed as a shipping order."
                )
        else:
            # If no city specified and couldn't be inferred, assume it's local delivery
            is_local_delivery = True
            logger.debug(
                f"No city specified for order {order_number}, assuming local delivery"
            )

        return {"city": city, "is_local_delivery": is_local_delivery}

    def _extract_building_code_for_local(
        self,
        order_number: str,
        order_remarks: str,
        shipping_address: str,
        address1: str,
        address2: str,
    ) -> str:
        """Extract building code for local deliveries using 3 priority levels."""
        building_code = None

        # PRIORITY 1: Check order remarks FIRST for building codes
        if order_remarks:
            logger.info(
                f"PRIORITY 1: Checking order remarks for order {order_number}: '{order_remarks[:100]}...'"
            )
            building_code = extract_building_code_from_location(order_remarks)
            if building_code:
                logger.info(
                    f"Found building code '{building_code}' directly in order remarks for order {order_number}"
                )

        # PRIORITY 2: If no building code in remarks, check alternative location from remarks
        if not building_code:
            logger.debug(
                f"PRIORITY 2: Checking alternative location patterns in remarks for order {order_number}"
            )
            alternative_location = self._extract_delivery_location_from_remarks(
                order_remarks
            )
            if alternative_location:
                logger.debug(
                    f"Found alternative location in remarks: '{alternative_location}'"
                )
                building_code = extract_building_code_from_location(
                    alternative_location
                )
                if building_code:
                    logger.info(
                        f"Found building code '{building_code}' from alternative location in remarks: '{alternative_location}' for order {order_number}"
                    )

        # PRIORITY 3: If still no building code, check shipping addresses
        if not building_code:
            logger.info(
                f"PRIORITY 3: Checking shipping addresses for order {order_number}. address1='{address1}', address2='{address2}', shipping_address='{shipping_address}'"
            )

            # Try extracting building code from address2
            if address2:
                logger.info(
                    f"Attempting to extract building code from address2: '{address2}'"
                )
                building_code = extract_building_code_from_location(address2)
                if building_code:
                    logger.info(
                        f"Found building code '{building_code}' from address2 using location patterns: '{address2}'"
                    )

            # Try location extraction on combined address
            if not building_code and shipping_address:
                logger.info(
                    f"Attempting to extract building code from combined shipping address: '{shipping_address}'"
                )
                building_code = extract_building_code_from_location(shipping_address)
                if building_code:
                    logger.info(
                        f"Found building code '{building_code}' from combined shipping address using location patterns: '{shipping_address}'"
                    )

            # Try ArcGIS matching
            if not building_code:
                building_code = get_building_abbreviation(None, shipping_address)
                if building_code:
                    logger.info(
                        f"Found building code '{building_code}' from shipping address via ArcGIS: '{shipping_address}'"
                    )

            if not building_code and address2:
                building_code = get_building_abbreviation(None, address2)
                if building_code:
                    logger.info(
                        f"Found building code '{building_code}' from address2 via ArcGIS: '{address2}'"
                    )

        # Final fallback logic
        if building_code:
            logger.info(
                f"Using building code as delivery_location for order {order_number}: '{building_code}'"
            )
            return building_code

        # Try fallback extraction
        logger.debug(
            f"No building code found yet, attempting final extraction from fallback strings for order {order_number}"
        )
        alternative_location = (
            self._extract_delivery_location_from_remarks(order_remarks)
            if order_remarks
            else None
        )

        if alternative_location:
            logger.debug(
                f"Attempting final extraction from alternative_location: '{alternative_location}'"
            )
            building_code = extract_building_code_from_location(alternative_location)
            if building_code:
                logger.info(
                    f"Extracted building code '{building_code}' from alternative_location fallback for order {order_number}"
                )
                return building_code

            building_code = extract_building_code_from_location(shipping_address)
            if building_code:
                logger.info(
                    f"Extracted building code '{building_code}' from shipping_address fallback for order {order_number}"
                )
                return building_code

            delivery_location = alternative_location or shipping_address
            logger.info(
                f"No building code found, using raw fallback delivery_location for order {order_number}: '{delivery_location}'"
            )
            return delivery_location

        # No alternative location
        building_code = extract_building_code_from_location(shipping_address)
        if building_code:
            logger.info(
                f"Extracted building code '{building_code}' from shipping_address fallback for order {order_number}"
            )
            return building_code

        logger.info(
            f"No building code found, using raw shipping_address as delivery_location for order {order_number}: '{shipping_address}'"
        )
        return shipping_address

    def _determine_delivery_location(
        self,
        order_number: str,
        is_local_delivery: bool,
        shipping_address: str,
        order_remarks: str,
        address1: str,
        address2: str,
        custom_fields: Dict[str, Any],
    ) -> str:
        """Determine the final delivery location based on order type and available data."""
        if is_local_delivery:
            return self._extract_building_code_for_local(
                order_number, order_remarks, shipping_address, address1, address2
            )

        # For non-local deliveries
        alternative_location = self._extract_delivery_location_from_remarks(
            order_remarks
        )

        # Map to building abbreviation if possible
        if alternative_location:
            mapped_abbreviation = get_building_abbreviation(None, alternative_location)
            if mapped_abbreviation:
                logger.info(
                    f"Mapped alternative location '{alternative_location}' to building code '{mapped_abbreviation}' via ArcGIS for non-local order {order_number}"
                )
                return mapped_abbreviation
            logger.info(
                f"Using raw alternative_location for non-local order {order_number}: '{alternative_location}'. Note: This order is outside Bryan/College Station"
            )
            return alternative_location

        # Fall back to shipping address for non-local
        mapped_abbreviation = get_building_abbreviation(None, shipping_address)
        if mapped_abbreviation:
            logger.info(
                f"Mapped shipping address '{shipping_address}' to building code '{mapped_abbreviation}' via ArcGIS for non-local order {order_number}"
            )
            return mapped_abbreviation

        logger.info(
            f"Using raw shipping_address for non-local order {order_number}: '{shipping_address}'. Note: This order is outside Bryan/College Station"
        )
        return shipping_address

    def _update_existing_order(
        self,
        existing_order: Order,
        inflow_data: Dict[str, Any],
        delivery_location: str,
        user_id: Optional[str] = None,
    ) -> Order:
        """Update an existing order with new inflow data."""
        order_remarks = inflow_data.get("orderRemarks", "")
        shipping_address_obj = self._as_dict(inflow_data.get("shippingAddress"))
        email = inflow_data.get("email", "")

        existing_order.inflow_data = self._preserve_partial_split_inflow_snapshot(
            existing_order,
            inflow_data,
        )
        existing_order.delivery_location = delivery_location
        existing_order.order_remarks = order_remarks
        existing_order.shipping_address = shipping_address_obj.get("address1", "")
        existing_order.shipping_address2 = shipping_address_obj.get("address2", "")
        existing_order.shipping_city = shipping_address_obj.get("city", "")
        existing_order.shipping_state = shipping_address_obj.get("stateProvince", "")
        existing_order.shipping_zip = shipping_address_obj.get("postalCode", "")
        existing_order.contact_name = inflow_data.get("contactName", "")
        existing_order.contact_email = email
        existing_order.customer_id = inflow_data.get("customerId", "")
        existing_order.customer_name = inflow_data.get("customerName", "")
        existing_order.updated_at = datetime.utcnow()

        self.db.commit()
        self.db.refresh(existing_order)

        # Audit logging
        audit_service = AuditService(self.db)
        audit_service.log_action(
            entity_type="order",
            entity_id=str(existing_order.id),
            action="updated_from_inflow",
            user_id=user_id,
            description=f"Order updated from Inflow webhook: {existing_order.order_number}",
            audit_metadata={
                "order_number": existing_order.order_number,
                "delivery_location": delivery_location,
                "inflow_order_id": inflow_data.get("salesOrderId"),
            },
        )

        logger.info(
            f"Updated existing order {existing_order.order_number} from Inflow webhook"
        )
        return existing_order

    def _create_new_order(
        self,
        inflow_data: Dict[str, Any],
        delivery_location: str,
        order_number: str,
        user_id: Optional[str] = None,
    ) -> Order:
        """Create a new order from inflow data."""
        order_remarks = inflow_data.get("orderRemarks", "")
        shipping_address_obj = self._as_dict(inflow_data.get("shippingAddress"))
        email = inflow_data.get("email", "")

        # Check if customer is flagged as a "problem customer" from notes
        customer_notes = inflow_data.get("customerNotes", "")
        is_problem_customer = self._check_problem_customer(customer_notes)

        new_order = Order(
            order_number=order_number,
            inflow_data=inflow_data,
            delivery_location=delivery_location,
            order_remarks=order_remarks,
            shipping_address=shipping_address_obj.get("address1", ""),
            shipping_address2=shipping_address_obj.get("address2", ""),
            shipping_city=shipping_address_obj.get("city", ""),
            shipping_state=shipping_address_obj.get("stateProvince", ""),
            shipping_zip=shipping_address_obj.get("postalCode", ""),
            contact_name=inflow_data.get("contactName", ""),
            contact_email=email,
            customer_id=inflow_data.get("customerId", ""),
            customer_name=inflow_data.get("customerName", ""),
            status=OrderStatus.NEW,
            is_problem_customer=is_problem_customer,
        )

        self.db.add(new_order)
        self.db.commit()
        self.db.refresh(new_order)

        # Audit logging
        audit_service = AuditService(self.db)
        audit_service.log_action(
            entity_type="order",
            entity_id=str(new_order.id),
            action="created_from_inflow",
            user_id=user_id,
            description=f"Order created from Inflow webhook: {order_number}",
            audit_metadata={
                "order_number": order_number,
                "delivery_location": delivery_location,
                "is_problem_customer": is_problem_customer,
                "inflow_order_id": inflow_data.get("salesOrderId"),
            },
        )

        logger.info(
            f"Created new order {order_number} from Inflow webhook (problem_customer={is_problem_customer})"
        )
        return new_order

    def create_order_from_inflow(self, inflow_data: Dict[str, Any]) -> Order:
        """Create or update order from Inflow data"""
        order_number = inflow_data.get("orderNumber")
        if not order_number:
            raise ValidationError("Order number is required", field="orderNumber")

        # Extract order remarks and shipping addresses
        order_remarks = inflow_data.get("orderRemarks", "")
        shipping_addr_obj = self._as_dict(inflow_data.get("shippingAddress"))
        address1 = shipping_addr_obj.get("address1", "")
        address2 = shipping_addr_obj.get("address2", "")

        # Combine address1 and address2 if both exist
        shipping_address_parts = [part for part in [address1, address2] if part]
        shipping_address = (
            " ".join(shipping_address_parts) if shipping_address_parts else address1
        )

        # Check if order is in Bryan/College Station for delivery routing
        city = (
            shipping_addr_obj.get("city", "").strip()
            if shipping_addr_obj.get("city")
            else ""
        )

        # If city is missing, try to detect it from the full address string for common non-local locations (like Houston)
        if not city and shipping_address:
            if "HOUSTON" in shipping_address.upper():
                city = "Houston"
                logger.info(
                    f"City not specified but 'Houston' found in address for order {order_number}. inferred_city='Houston'"
                )

        is_local_delivery = False

        # Determine if this is a local delivery (Bryan/College Station) or shipping order
        if city:
            city_upper = city.upper()
            is_local_delivery = city_upper in ("BRYAN", "COLLEGE STATION")
            if not is_local_delivery:
                logger.info(
                    f"Order {order_number} is outside Bryan/College Station (city: '{city}'). This will be processed as a shipping order."
                )
        else:
            # If no city specified and couldn't be inferred, assume it's local delivery (might be data issue)
            is_local_delivery = True
            logger.debug(
                f"No city specified for order {order_number}, assuming local delivery"
            )

        building_code = None

        if is_local_delivery:
            # For local deliveries, try to extract building codes
            # PRIORITY 1: Check order remarks FIRST for building codes
            if order_remarks:
                logger.info(
                    f"PRIORITY 1: Checking order remarks for order {order_number}: '{order_remarks[:100]}...'"
                )
                # Try to extract building code directly from order remarks
                building_code = extract_building_code_from_location(order_remarks)
                if building_code:
                    logger.info(
                        f"✓ Found building code '{building_code}' directly in order remarks for order {order_number}"
                    )
                else:
                    logger.debug(
                        f"✗ No building code found directly in order remarks for order {order_number}"
                    )

            # PRIORITY 2: If no building code in remarks, check alternative location from remarks
            if not building_code:
                logger.debug(
                    f"PRIORITY 2: Checking alternative location patterns in remarks for order {order_number}"
                )
                alternative_location = self._extract_delivery_location_from_remarks(
                    order_remarks
                )
                if alternative_location:
                    logger.debug(
                        f"Found alternative location in remarks: '{alternative_location}'"
                    )
                    building_code = extract_building_code_from_location(
                        alternative_location
                    )
                    if building_code:
                        logger.info(
                            f"✓ Found building code '{building_code}' from alternative location in remarks: '{alternative_location}' for order {order_number}"
                        )
                    else:
                        logger.debug(
                            f"✗ No building code extracted from alternative location '{alternative_location}'"
                        )
                else:
                    logger.debug(
                        f"No alternative location patterns found in remarks for order {order_number}"
                    )

            # PRIORITY 3: If still no building code, check shipping addresses
            if not building_code:
                logger.info(
                    f"PRIORITY 3: Checking shipping addresses for order {order_number}. address1='{address1}', address2='{address2}', shipping_address='{shipping_address}'"
                )

                # First try extracting building code from address2 using location patterns (e.g., "Wehner Bldg")
                if address2:
                    logger.info(
                        f"Attempting to extract building code from address2: '{address2}'"
                    )
                    building_code = extract_building_code_from_location(address2)
                    if building_code:
                        logger.info(
                            f"✓ Found building code '{building_code}' from address2 using location patterns: '{address2}'"
                        )
                    else:
                        logger.info(
                            f"✗ No building code extracted from address2: '{address2}'"
                        )

                # If no building code found, try location extraction on combined address
                if not building_code and shipping_address:
                    logger.info(
                        f"Attempting to extract building code from combined shipping address: '{shipping_address}'"
                    )
                    building_code = extract_building_code_from_location(
                        shipping_address
                    )
                    if building_code:
                        logger.info(
                            f"✓ Found building code '{building_code}' from combined shipping address using location patterns: '{shipping_address}'"
                        )
                    else:
                        logger.info(
                            f"✗ No building code extracted from combined shipping address: '{shipping_address}'"
                        )

                # If no building code found, try ArcGIS matching on combined address
                if not building_code:
                    building_code = get_building_abbreviation(None, shipping_address)
                    if building_code:
                        logger.info(
                            f"Found building code '{building_code}' from shipping address via ArcGIS: '{shipping_address}'"
                        )

                # If still no building code, try ArcGIS matching on address2
                if not building_code and address2:
                    building_code = get_building_abbreviation(None, address2)
                    if building_code:
                        logger.info(
                            f"Found building code '{building_code}' from address2 via ArcGIS: '{address2}'"
                        )

            # Use building code if found, otherwise try to extract from fallback strings
            if building_code:
                delivery_location = building_code
                logger.info(
                    f"Using building code as delivery_location for order {order_number}: '{delivery_location}'"
                )
            else:
                # Fallback: try to extract building code from alternative location or shipping address
                # before using them as-is
                logger.debug(
                    f"No building code found yet, attempting final extraction from fallback strings for order {order_number}"
                )

                # Get alternative location if not already extracted
                alternative_location = (
                    self._extract_delivery_location_from_remarks(order_remarks)
                    if order_remarks
                    else None
                )

                # Try to extract building code from alternative location first
                if alternative_location:
                    logger.debug(
                        f"Attempting final extraction from alternative_location: '{alternative_location}'"
                    )
                    building_code = extract_building_code_from_location(
                        alternative_location
                    )
                    if building_code:
                        delivery_location = building_code
                        logger.info(
                            f"✓ Extracted building code '{building_code}' from alternative_location fallback for order {order_number}"
                        )
                    else:
                        # Try to extract from shipping address
                        logger.debug(
                            f"Attempting final extraction from shipping_address: '{shipping_address}'"
                        )
                        building_code = extract_building_code_from_location(
                            shipping_address
                        )
                        if building_code:
                            delivery_location = building_code
                            logger.info(
                                f"✓ Extracted building code '{building_code}' from shipping_address fallback for order {order_number}"
                            )
                        else:
                            # Last resort: use alternative location or shipping address as-is
                            delivery_location = alternative_location or shipping_address
                            logger.info(
                                f"No building code found, using raw fallback delivery_location for order {order_number}: '{delivery_location}'"
                            )
                else:
                    # No alternative location, try shipping address
                    logger.debug(
                        f"No alternative location, attempting final extraction from shipping_address: '{shipping_address}'"
                    )
                    building_code = extract_building_code_from_location(
                        shipping_address
                    )
                    if building_code:
                        delivery_location = building_code
                        logger.info(
                            f"✓ Extracted building code '{building_code}' from shipping_address fallback for order {order_number}"
                        )
                    else:
                        # Last resort: use shipping address as-is
                        delivery_location = shipping_address
                        logger.info(
                            f"No building code found, using raw shipping_address as delivery_location for order {order_number}: '{delivery_location}'"
                        )
        else:
            # For shipping orders, use the city as delivery location
            delivery_location = city if city else shipping_address
            logger.info(
                f"Using city as delivery_location for shipping order {order_number}: '{delivery_location}'"
            )

        delivery_location = self._apply_delivery_location_overrides(
            inflow_data, delivery_location
        )

        # Check if order exists.
        # Prefer an exact inFlow order number match first so split parent/child
        # legs that share the same salesOrderId do not get updated ambiguously.
        sales_order_id = inflow_data.get("salesOrderId")
        existing = (
            self.db.query(Order).filter(Order.inflow_order_id == order_number).first()
        )
        if not existing and sales_order_id:
            existing = (
                self.db.query(Order)
                .filter(Order.inflow_sales_order_id == sales_order_id)
                .first()
            )

        if existing:
            # Update existing order - only update timestamp if data actually changed
            data_changed = False

            if existing.inflow_sales_order_id != inflow_data.get("salesOrderId"):
                existing.inflow_sales_order_id = inflow_data.get("salesOrderId")
                data_changed = True

            new_recipient_name = inflow_data.get("contactName")
            # TODO: Add logic for more sophisticated recipient name ingestion from various sources
            if existing.recipient_name != new_recipient_name:
                existing.recipient_name = new_recipient_name
                data_changed = True

            if existing.recipient_contact != inflow_data.get("email"):
                existing.recipient_contact = inflow_data.get("email")
                data_changed = True

            if existing.delivery_location != delivery_location:
                existing.delivery_location = delivery_location
                data_changed = True

            if existing.po_number != inflow_data.get("poNumber"):
                existing.po_number = inflow_data.get("poNumber")
                data_changed = True

            # Only update timestamp if data actually changed
            if data_changed:
                existing.updated_at = datetime.utcnow()

            existing.inflow_data = self._preserve_partial_split_inflow_snapshot(
                existing,
                inflow_data,
            )  # Always update to keep latest data without clobbering partial splits

            # Don't overwrite manual status changes - keep existing status

            self.db.commit()
            self.db.refresh(existing)
            return existing
        else:
            # Create new order with IntegrityError handling for duplicate webhooks (Issue #40)
            try:
                order_date = None
                if "orderDate" in inflow_data and inflow_data.get("orderDate"):
                    try:
                        # Parse Inflow date string (format may vary)
                        order_date_str = inflow_data.get("orderDate")
                        if isinstance(order_date_str, str):
                            # Try common date formats
                            for fmt in [
                                "%Y-%m-%dT%H:%M:%S",
                                "%Y-%m-%dT%H:%M:%S.%f",
                                "%Y-%m-%d %H:%M:%S",
                                "%Y-%m-%d",
                            ]:
                                try:
                                    order_date = datetime.strptime(
                                        order_date_str.split("+")[0].split("Z")[0], fmt
                                    )
                                    break
                                except ValueError:
                                    continue
                    except Exception as e:
                        logger.debug(
                            f"Could not parse orderDate '{inflow_data.get('orderDate')}' for order {order_number}: {e}"
                        )

                # Use orderDate if available, otherwise use current time
                created_time = order_date if order_date else datetime.utcnow()

                order = Order(
                    inflow_order_id=order_number,
                    inflow_sales_order_id=inflow_data.get("salesOrderId"),
                    recipient_name=inflow_data.get("contactName"),
                    recipient_contact=inflow_data.get("email"),
                    delivery_location=delivery_location,
                    po_number=inflow_data.get("poNumber"),
                    status=OrderStatus.PICKED.value,
                    inflow_data=inflow_data,
                    created_at=created_time,
                    updated_at=created_time,  # Set to same as created_at initially
                )
                self.db.add(order)
                self.db.commit()
                self.db.refresh(order)

                # Create AuditLog entry for initial 'picked' status in timeline
                audit_log = AuditLog(
                    order_id=order.id,
                    changed_by="system",
                    from_status=None,  # No previous status - order was just created
                    to_status=OrderStatus.PICKED.value,
                    reason="Order ingested from inFlow",
                    timestamp=datetime.utcnow(),  # Use ingestion time (not orderDate) for audit trail
                )
                self.db.add(audit_log)
                self._record_status_history(
                    order=order,
                    from_status=None,
                    to_status=OrderStatus.PICKED.value,
                    actor_identifier="system",
                    metadata={"reason": "Order ingested from inFlow"},
                )
                self.db.commit()

                # Also log to system audit log for full traceability
                audit_service = AuditService(self.db)
                audit_service.log_order_action(
                    order_id=str(order.id),
                    action="imported_from_inflow",
                    user_id="system",  # Automated import
                    description=f"Order imported from inFlow",
                    audit_metadata={
                        "inflow_order_id": order_number,
                        "inflow_sales_order_id": inflow_data.get("salesOrderId"),
                        "source": "inflow_webhook",
                        "order_type": "shipping"
                        if self._is_shipping_order(order)
                        else "delivery",
                    },
                )

                return order
            except IntegrityError:
                self.db.rollback()
                # Another request created it — fetch and update instead
                existing = self.db.query(Order).filter(Order.inflow_order_id == order_number).first()
                if existing:
                    existing.inflow_data = self._preserve_partial_split_inflow_snapshot(
                        existing,
                        inflow_data,
                    )
                    existing.updated_at = datetime.utcnow()
                    self.db.commit()
                    self.db.refresh(existing)
                    return existing
                raise

    def _send_order_details_email(
        self, order: Order, generated_by: Optional[str] = None
    ) -> bool:
        """
        Send Order Details PDF email to recipient after picklist generation.

        Flow:
        1. Check SharePoint for existing PDF
        2. If not found, generate PDF and upload to SharePoint
        3. Send email with PDF

        This runs as a best-effort operation - errors are logged but don't fail the picklist generation.
        """
        from app.services.pdf_service import pdf_service
        from app.services.email_service import email_service
        from app.services.inflow_service import InflowService

        # Get recipient email
        recipient_email = order.recipient_contact
        order_number = order.inflow_order_id
        pdf_filename = f"{order_number}.pdf"

        try:
            if not order.inflow_data:
                logger.warning(
                    f"No inFlow data for order {order_number}, skipping Order Details generation"
                )
                return False

            inflow_service = InflowService()
            order_details_data = inflow_service.build_remaining_order_view(
                order.inflow_data
            )
            remainder_mode = bool(
                getattr(order, "remainder_order_id", None) and not getattr(order, "parent_order_id", None)
            )
            force_regenerate_pdf = remainder_mode
            if remainder_mode:
                remainder_view = OrderSplittingService(self.db).build_parent_remainder_document_view(order)
                if remainder_view is not None:
                    order_details_data = remainder_view
            has_partial_remaining = bool(order_details_data.get("lines")) and (
                order_details_data.get("lines") != order.inflow_data.get("lines", [])
            )
            force_regenerate_pdf = force_regenerate_pdf or has_partial_remaining
            pdf_source_data = order_details_data if (has_partial_remaining or force_regenerate_pdf) else order.inflow_data

            pdf_bytes = None
            order_details_path = None
            pdf_content = None

            # Step 1: Check SharePoint first for existing PDF
            if not force_regenerate_pdf:
                try:
                    from app.services.sharepoint_service import get_sharepoint_service

                    sp_service = get_sharepoint_service()

                    if sp_service.is_enabled:
                        logger.info(
                            f"Checking SharePoint for existing Order Details PDF: {pdf_filename}"
                        )
                        existing_pdf = sp_service.download_file(
                            "order-details", pdf_filename
                        )

                        if existing_pdf:
                            logger.info(
                                f"Found existing Order Details PDF in SharePoint: {pdf_filename}"
                            )
                            pdf_bytes = existing_pdf
                            order_details_path = sp_service.get_file_url(
                                "order-details", pdf_filename
                            )
                except Exception as e:
                    logger.warning(f"Error checking SharePoint for existing PDF: {e}")

            # Step 2: If not found in SharePoint, generate and upload
            if pdf_bytes is None:
                logger.info(f"Generating Order Details PDF for order {order_number}")
                pdf_bytes = pdf_service.generate_order_details_pdf(pdf_source_data)

            # Normalize PDF content to raw bytes for storage/email.
            pdf_content = pdf_bytes.getvalue() if hasattr(pdf_bytes, "getvalue") else pdf_bytes

            # Save Order Details PDF locally first, then upload to SharePoint if available.
            od_dir = self._local_doc_path("orders", pdf_filename).parent
            od_dir.mkdir(parents=True, exist_ok=True)
            od_path = self._local_doc_path("orders", pdf_filename)
            od_path.write_bytes(pdf_content)
            logger.info(f"Order Details PDF saved locally: {od_path}")
            order_details_path = str(od_path)

            try:
                from app.services.sharepoint_service import get_sharepoint_service

                sp_service = get_sharepoint_service()
                if not sp_service.is_enabled:
                    raise RuntimeError("SharePoint storage is not enabled")

                uploaded_url = sp_service.upload_file(pdf_content, "order-details", pdf_filename)
                logger.info(f"Order Details PDF uploaded to SharePoint: {uploaded_url}")
                order_details_path = uploaded_url
            except Exception as e:
                logger.error("SharePoint upload failed for Order Details: %s", e)
                raise

            # Update order with Order Details path
            order.order_details_path = order_details_path
            order.order_details_generated_at = datetime.utcnow()
            self.db.commit()

            # Check if email sending is configured
            if not email_service.is_configured():
                logger.debug(
                    f"Power Automate email not configured, skipping Order Details email for order {order_number}"
                )
                return False

            if not recipient_email:
                logger.warning(
                    f"No recipient email for order {order_number}, skipping Order Details email"
                )
                return False

            # Step 3: Send email with PDF
            customer_name = order.recipient_name or "Customer"

            success = email_service.send_order_details_email(
                to_address=recipient_email,
                order_number=order_number,
                customer_name=customer_name,
                pdf_content=pdf_content,
            )

            if success:
                logger.info(
                    f"Order Details email sent to {recipient_email} for order {order_number}"
                )

                # Audit log the email
                audit_service = AuditService(self.db)
                audit_service.log_order_action(
                    order_id=str(order.id),
                    action="order_details_email_sent",
                    user_id=generated_by or "system",
                    description=f"Order Details PDF emailed to {recipient_email}",
                    audit_metadata={
                        "recipient_email": recipient_email,
                        "order_number": order_number,
                        "pdf_path": order_details_path,
                    },
                )
                if not self._mark_order_details_sent(order, generated_by):
                    logger.error(
                        "Order Details email succeeded for %s but remarks marker update failed",
                        order_number,
                    )
                    return False
                return True
            else:
                logger.error(
                    f"Failed to send Order Details email to {recipient_email} for order {order_number}"
                )
                return False

        except Exception as e:
            # Log error and re-raise so required SharePoint/upload/email failures
            # surface to the caller instead of being masked as success.
            logger.error(
                f"Error generating/sending Order Details for order {order.inflow_order_id}: {e}"
            )
            raise

    def transition_shipping_workflow(
        self,
        order_id: Union[UUID, str],
        new_status: ShippingWorkflowStatus,
        carrier_name: Optional[str] = None,
        tracking_number: Optional[str] = None,
        updated_by: Optional[str] = None,
        expected_updated_at: Optional[datetime] = None,
    ) -> Order:
        """Transition shipping workflow status with validation"""
        order_id_str = str(order_id)
        order = (
            self.db.query(Order)
            .filter(Order.id == order_id_str)
            .with_for_update()
            .first()
        )
        if not order:
            raise NotFoundError("Order", str(order_id))

        self.assert_not_stale(order, expected_updated_at)

        if order.status != OrderStatus.SHIPPING.value:
            raise ValidationError(
                "Order must be in Shipping status to update shipping workflow"
            )

        # Validate blocking requirements
        current_status = (
            order.shipping_workflow_status or ShippingWorkflowStatus.WORK_AREA.value
        )

        if (
            new_status == ShippingWorkflowStatus.DOCK
            and current_status != ShippingWorkflowStatus.WORK_AREA.value
        ):
            raise ValidationError("Order must be in Work Area before moving to Dock")

        if (
            new_status == ShippingWorkflowStatus.SHIPPED
            and current_status != ShippingWorkflowStatus.DOCK.value
        ):
            raise ValidationError("Order must be at Dock before marking as Shipped")

        # Update fields
        order.shipping_workflow_status = new_status.value
        order.shipping_workflow_status_updated_at = datetime.utcnow()
        order.shipping_workflow_status_updated_by = updated_by
        order.updated_at = datetime.utcnow()

        if new_status == ShippingWorkflowStatus.SHIPPED:
            order.shipped_to_carrier_at = datetime.utcnow()
            order.shipped_to_carrier_by = updated_by
            if carrier_name:
                order.carrier_name = carrier_name
            if tracking_number:
                order.tracking_number = tracking_number

            # Auto-transition to Delivered status when shipped
            old_status = order.status
            order.status = OrderStatus.DELIVERED.value
            # Clean up local document files now that delivery is complete
            self._cleanup_order_documents(order)

            # Create AuditLog entry for timeline display
            audit_log = AuditLog(
                order_id=order.id,
                changed_by=updated_by or "system",
                from_status=old_status,
                to_status=OrderStatus.DELIVERED.value,
                reason=f"Shipped via {carrier_name or 'carrier'}"
                + (f" (Tracking: {tracking_number})" if tracking_number else ""),
                timestamp=datetime.utcnow(),
            )
            self.db.add(audit_log)
            self._record_status_history(
                order=order,
                from_status=old_status,
                to_status=OrderStatus.DELIVERED.value,
                actor_identifier=updated_by,
                metadata={
                    "reason": f"Shipped via {carrier_name or 'carrier'}"
                    + (f" (Tracking: {tracking_number})" if tracking_number else ""),
                },
            )

            # Create AuditLog entry for timeline display
            audit_log = AuditLog(
                order_id=order.id,
                changed_by=updated_by or "system",
                from_status=old_status,
                to_status=OrderStatus.DELIVERED.value,
                reason=f"Shipped via {carrier_name or 'carrier'}"
                + (f" (Tracking: {tracking_number})" if tracking_number else ""),
                timestamp=datetime.utcnow(),
            )
            self.db.add(audit_log)
            self._record_status_history(
                order=order,
                from_status=old_status,
                to_status=OrderStatus.DELIVERED.value,
                actor_identifier=updated_by,
                metadata={
                    "reason": f"Shipped via {carrier_name or 'carrier'}"
                    + (f" (Tracking: {tracking_number})" if tracking_number else ""),
                },
            )

        self.db.commit()
        self.db.refresh(order)

        # Also log to system audit log for full traceability
        audit_service = AuditService(self.db)
        audit_service.log_order_action(
            order_id=str(order_id),
            action=f"shipping_workflow_{new_status.value}",
            user_id=updated_by or "unknown",
            description=f"Shipping workflow updated to {new_status.display_name}",
            audit_metadata={
                "carrier_name": carrier_name,
                "tracking_number": tracking_number,
            },
        )

        return order

    def generate_bundled_documents(
        self, order_id: Union[UUID, str], signature_data: dict
    ) -> tuple[str, str]:
        """Generate bundled documents directly in SharePoint (no local storage).
        Returns (signed_picklist_sp_url, bundle_sp_url)."""
        order_id_str = str(order_id)
        order = self.db.query(Order).filter(Order.id == order_id_str).first()
        if not order:
            raise NotFoundError("Order", str(order_id))

        if not order.picklist_path or not order.qa_path:
            raise ValidationError(
                "Order missing picklist or QA data",
                details={
                    "has_picklist": bool(order.picklist_path),
                    "has_qa": bool(order.qa_path),
                },
            )

        import tempfile
        from pathlib import Path

        base_filename = f"{order.inflow_order_id or order.id}"
        
        # Ensure picklist is locally available (download if it's a SharePoint URL)
        picklist_temp_to_delete = None
        if order.picklist_path.startswith("http"):
            from app.services.sharepoint_service import get_sharepoint_service
            sp = get_sharepoint_service()
            picklist_bytes = sp.download_file("picklists", f"{base_filename}.pdf")
            if not picklist_bytes:
                raise NotFoundError("Picklist", base_filename)
            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp.write(picklist_bytes)
            tmp.close()
            local_picklist_path = tmp.name
            picklist_temp_to_delete = local_picklist_path
        else:
            local_picklist_path = order.picklist_path

        # Apply signature to get signed picklist (returns temp file path)
        logger.info("SIG_BUNDLE calling _apply_signature_to_pdf with page_width=%s page_height=%s",
                     signature_data.get("page_width"), signature_data.get("page_height"))
        signed_temp = self._apply_signature_to_pdf(local_picklist_path, signature_data)

        # Generate QA PDF (returns temp file path)
        qa_temp = self._generate_qa_pdf(order.qa_data, order)

        # Save signed picklist to local storage
        signed_local_dir = self._local_doc_path("signed", f"{base_filename}_signed.pdf").parent
        signed_local_dir.mkdir(parents=True, exist_ok=True)
        signed_local_path = self._local_doc_path("signed", f"{base_filename}_signed.pdf")
        shutil.copy2(signed_temp, signed_local_path)
        logger.info(f"Signed picklist saved locally: {signed_local_path}")

        # Save QA PDF to local storage
        qa_local_dir = self._local_doc_path("qa", f"{base_filename}_qa.pdf").parent
        qa_local_dir.mkdir(parents=True, exist_ok=True)
        qa_local_path = self._local_doc_path("qa", f"{base_filename}_qa.pdf")
        shutil.copy2(qa_temp, qa_local_path)
        logger.info(f"QA PDF saved locally: {qa_local_path}")

        # Create bundle from signed + QA PDFs
        tmp_bundle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        bundle_temp = tmp_bundle.name
        tmp_bundle.close()
        self._bundle_pdfs([signed_temp, qa_temp], bundle_temp)
        bundle_local_dir = self._local_doc_path("bundles", f"{base_filename}_bundle.pdf").parent
        bundle_local_dir.mkdir(parents=True, exist_ok=True)
        bundle_local_path = self._local_doc_path("bundles", f"{base_filename}_bundle.pdf")
        shutil.copy2(bundle_temp, bundle_local_path)
        logger.info(f"Bundle saved locally: {bundle_local_path}")

        # All documents saved locally — now upload to SharePoint
        try:
            from app.services.sharepoint_service import get_sharepoint_service
            sp_service = get_sharepoint_service()
            if not sp_service.is_enabled:
                raise RuntimeError("SharePoint storage is not enabled")

            # Upload signed picklist
            with open(signed_local_path, "rb") as f:
                signed_bytes = f.read()
            signed_sp_url = sp_service.upload_file(signed_bytes, "signed", f"{base_filename}_signed.pdf")
            logger.info(f"Signed picklist uploaded to SharePoint: {signed_sp_url}")

            # Upload QA PDF
            with open(qa_local_path, "rb") as f:
                qa_bytes = f.read()
            qa_sp_url = sp_service.upload_file(qa_bytes, "qa", f"{base_filename}_qa.pdf")
            logger.info(f"QA PDF uploaded to SharePoint: {qa_sp_url}")

            # Upload bundle
            with open(bundle_local_path, "rb") as f:
                bundle_bytes = f.read()
            bundle_sp_url = sp_service.upload_file(bundle_bytes, "bundles", f"{base_filename}_bundle.pdf")
            logger.info(f"Bundle uploaded to SharePoint: {bundle_sp_url}")

            # Persist SharePoint URLs on order (source of truth)
            order.signed_picklist_path = signed_sp_url
            if hasattr(order, 'bundle_path'):
                order.bundle_path = bundle_sp_url
        except Exception as e:
            logger.error(f"SharePoint upload failed in generate_bundled_documents: {e}")
            raise  # No local fallback

        # Clean up temp files
        for p in [signed_temp, qa_temp, bundle_temp]:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Failed to delete temp file {p}: {e}")
        if picklist_temp_to_delete:
            try:
                Path(picklist_temp_to_delete).unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Failed to delete temp picklist {picklist_temp_to_delete}: {e}")

        return signed_sp_url, bundle_sp_url


    def _apply_signature_to_pdf(self, pdf_path: str, signature_data: dict) -> str:
        """Apply signature overlay to existing PDF"""
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
        import base64
        import io
        from PIL import Image
        import tempfile
        import os

        # Extract signature data
        signature_b64 = signature_data.get("signature_image", "")
        placements = signature_data.get("placements", [])

        # Backward compatibility for single placement
        if not placements and "page_number" in signature_data:
            placements = [
                {
                    "page_number": signature_data.get("page_number", 1),
                    # If position is missing, default to some safe spot?
                    # Original code defaulted to x=50, y=60.
                    # Schema might return None for position if not passed.
                    "x": signature_data.get("position", {}).get("x", 50),
                    "y": signature_data.get("position", {}).get("y", 60),
                    "width": 100,  # Default width if not specified in old format? Old format didn't have width/height in backend logic?
                    # Wait, old logic: c.drawImage(..., width=page_width, height=page_height)
                    # It stretched the canvas capture to the FULL PAGE.
                    # The design doc says: "Front end sends canvas capture that matches PDF aspect ratio...".
                    # If we are refactoring, we need to handle the new "sticker" style.
                    # If old style: we should probably stick to old behavior or migrating is tricky without frontend changes first.
                    # BUT: The plan says "Refactor... to accept image + placements".
                    # I will assume "placements" means "sticker placements".
                    # If legacy call comes in, I will map it to "full page stamp" if that was the old behavior?
                    # Old behavior: c.drawImage(..., 0, 0, width=page_width, height=page_height).
                    # So if no placements, treat as legacy full-page overlay.
                    "legacy_full_page": True,
                }
            ]

        if not signature_b64:
            return pdf_path

        try:
            # Decode base64 signature image
            header, encoded = signature_b64.split(",", 1)
            signature_data_bytes = base64.b64decode(encoded)
            signature_image = Image.open(io.BytesIO(signature_data_bytes))
        except Exception as e:
            logger.error(f"Error processing signature image: {e}")
            return pdf_path

        # Read the original PDF
        reader = PdfReader(pdf_path)
        writer = PdfWriter()

        # Group placements by page for efficiency
        # placements_by_page = { page_index: [placemen1, placement2] }
        placements_by_page = {}
        for p in placements:
            # Handle dictionary or object (if pydantic model passed directly, though dict expected here)
            if hasattr(p, "model_dump"):
                p = p.model_dump()

            page_num = p.get("page_number", 1)
            if page_num not in placements_by_page:
                placements_by_page[page_num] = []
            placements_by_page[page_num].append(p)

        # Create overlay pages
        # dict mapping page_index (0-based) to overlay_pdf_path
        overlay_map = {}

        try:
            for page_num, page_placements in placements_by_page.items():
                if page_num < 1 or page_num > len(reader.pages):
                    continue

                page_idx = page_num - 1
                page = reader.pages[page_idx]

                # Use frontend-provided page dimensions when available.
                # This eliminates coordinate mismatches between PDF.js (which
                # may use CropBox, rotation, etc.) and pypdf/Reportlab.
                import logging as _l
                _logger = _l.getLogger(__name__)
                from_frontend_w = signature_data.get("page_width")
                from_frontend_h = signature_data.get("page_height")
                _logger.info("SIG_BACKEND page_width=%s page_height=%s from_frontend_w=%s from_frontend_h=%s",
                             signature_data.get("page_width"), signature_data.get("page_height"),
                             from_frontend_w, from_frontend_h)
                if from_frontend_w and from_frontend_h:
                    page_width = float(from_frontend_w)
                    page_height = float(from_frontend_h)
                    origin_x = 0.0
                    origin_y = 0.0
                else:
                    visible = page.cropbox
                    page_width = float(visible.width)
                    page_height = float(visible.height)
                    origin_x = float(visible.left)
                    origin_y = float(visible.bottom)
                    rotation = page.get("/Rotate", 0) or 0
                    if rotation in (90, 270):
                        page_width, page_height = page_height, page_width

                with tempfile.NamedTemporaryFile(
                    suffix=".pdf", delete=False
                ) as overlay_file:
                    overlay_path = overlay_file.name
                    overlay_map[page_idx] = overlay_path

                c = canvas.Canvas(overlay_path, pagesize=(page_width, page_height))

                _logger.info("SIG_BACKEND overlay pagesize=%sx%s origin=%s,%s",
                             page_width, page_height, origin_x, origin_y)

                for placement in page_placements:
                    # Check for legacy full page flag
                    if placement.get("legacy_full_page"):
                        c.drawImage(
                            ImageReader(signature_image),
                            0 + origin_x,
                            0 + origin_y,
                            width=page_width,
                            height=page_height,
                            mask="auto",
                        )
                    else:
                        # New sticker placement
                        x = placement.get("x", 0)
                        y = placement.get("y", 0)
                        w = placement.get("width", 100)
                        h = placement.get("height", 50)

                        _logger.info("SIG_BACKEND draw placement placement_x=%s placement_y=%s w=%s h=%s origin=%s,%s draw_x=%s draw_y=%s",
                                     x, y, w, h, origin_x, origin_y, x + origin_x, y + origin_y)

                        # Inverting Y is handled by frontend (sending PDF coords).
                        # We just draw at (x, y). Use preserveAspectRatio=True?
                        # Design doc implies w/h are explicit.
                        c.drawImage(
                            ImageReader(signature_image),
                            x + origin_x,
                            y + origin_y,
                            width=w,
                            height=h,
                            mask="auto",
                            preserveAspectRatio=True,
                        )

                c.save()

            # Merge
            for i, page in enumerate(reader.pages):
                if i in overlay_map:
                    overlay_reader = PdfReader(overlay_map[i])
                    overlay_page = overlay_reader.pages[0]
                    # Use merge_transformed_page with identity transform to avoid
                    # any implicit coordinate transformations that merge_page may apply
                    page.merge_transformed_page(overlay_page, (1, 0, 0, 1, 0, 0), over=True, expand=False)
                writer.add_page(page)

            # Save signed PDF
            signed_path = pdf_path.replace(".pdf", "-signed.pdf")
            with open(signed_path, "wb") as f:
                writer.write(f)

            return signed_path

        finally:
            # Cleanup overlays
            for path in overlay_map.values():
                try:
                    os.unlink(path)
                except OSError as exc:
                    logger.warning(
                        "Failed to remove temporary signature overlay %s: %s", path, exc
                    )

    def _generate_qa_pdf(self, qa_data: dict, order: Order) -> str:
        """Generate QA checklist PDF from JSON data"""
        import tempfile
        from pathlib import Path

        filename = f"{order.inflow_order_id or order.id}-qa.pdf"
        # Create temporary file; caller will upload and then delete
        tmp_file = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        output_path = Path(tmp_file.name)
        tmp_file.close()  # Allow reportlab to write

        pdf = canvas.Canvas(str(output_path), pagesize=letter)
        width, height = letter

        # Header
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(50, height - 50, "Quality Assurance Checklist")

        # Order details
        pdf.setFont("Helvetica", 12)
        y_pos = height - 80
        pdf.drawString(50, y_pos, f"Order: {order.inflow_order_id}")
        pdf.drawString(
            50, y_pos - 20, f"Recipient: {order.recipient_name or 'Unknown'}"
        )
        pdf.drawString(50, y_pos - 40, f"Method: {qa_data.get('method', 'Unknown')}")
        pdf.drawString(
            50, y_pos - 60, f"Technician: {qa_data.get('technician', 'Unknown')}"
        )
        pdf.drawString(
            50, y_pos - 80, f"QA Signature: {qa_data.get('qaSignature', 'Unknown')}"
        )

        # Checklist items
        y_pos -= 120
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(50, y_pos, "Quality Assurance Checklist:")
        y_pos -= 30

        pdf.setFont("Helvetica", 11)

        # Handle both old and new QA formats
        if "items" in qa_data:
            # Old format: items array with id, label, passed
            checklist_items = qa_data["items"]
            for item in checklist_items:
                status = "PASS" if item.get("passed", False) else "FAIL"
                label = item.get("label", item.get("id", "Unknown"))
                pdf.drawString(70, y_pos, f"[{status}] {label}")
                y_pos -= 20
        else:
            # New detailed format: individual boolean fields
            checklist_items = [
                (
                    "verifyAssetTagSerialMatch",
                    "Asset tags applied and serial numbers match on device, sticker, and pick list",
                ),
                (
                    "verifyOrderDetailsTemplateSentAndElectronicPackingSlipSaved",
                    "Order details template sent and electronic packing slip saved",
                ),
                (
                    "verifyPackagedProperly",
                    "System and all materials packaged properly",
                ),
                (
                    "verifyPackingSlipSerialsMatch",
                    "Packing slip and picked items serial numbers match",
                ),
                (
                    "verifyBoxesLabeledCorrectly",
                    "Boxes labeled with correct order details and shipping labels marked out",
                ),
            ]

            for field, description in checklist_items:
                status = "PASS" if qa_data.get(field, False) else "FAIL"
                pdf.drawString(70, y_pos, f"[{status}] {description}")
                y_pos -= 20

        # Signature line
        y_pos -= 40
        pdf.setFont("Helvetica", 12)
        pdf.drawString(50, y_pos, "QA Technician Signature:")
        pdf.line(50, y_pos - 10, width - 50, y_pos - 10)
        pdf.drawString(50, y_pos - 25, qa_data.get("qaSignature", ""))

        pdf.save()
        return str(output_path)

    def _bundle_pdfs(self, pdf_paths: list[str], output_path: str) -> None:
        """Combine multiple PDFs into a single document."""

        from pypdf import PdfWriter

        writer = PdfWriter()
        for pdf_path in pdf_paths:
            writer.append(pdf_path)

        with open(output_path, "wb") as output_file:
            writer.write(output_file)
