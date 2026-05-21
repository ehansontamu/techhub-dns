import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Union
from uuid import UUID

from flask import g
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.delivery_run import DeliveryRun, DeliveryRunStatus, VehicleEnum
from app.models.order import Order, OrderStatus
from app.models.order_status_history import OrderStatusHistory
from app.models.user import User
from app.models.vehicle_checkout import VehicleCheckout
from app.services.audit_service import AuditService
from app.services.inflow_service import InflowService
from app.utils.exceptions import ConflictError, NotFoundError, ValidationError
from app.utils.timezone import get_date_in_cst, is_morning_in_cst, to_utc_iso_z

class DeliveryRunService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _normalize_stale_timestamp(value: datetime) -> datetime:
        normalized = (
            value.astimezone(timezone.utc).replace(tzinfo=None)
            if value.tzinfo
            else value
        )
        return normalized.replace(microsecond=(normalized.microsecond // 1000) * 1000)

    def _get_shipment_status(
        self, inflow_data: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not inflow_data:
            return {
                "is_fully_shipped": True,
                "total_ordered": 0,
                "total_shipped": 0,
                "remaining_items": [],
            }

        lines = inflow_data.get("lines", [])
        pack_lines = inflow_data.get("packLines", [])

        if not isinstance(lines, list):
            lines = []
        if not isinstance(pack_lines, list):
            pack_lines = []

        required: Dict[str, float] = {}
        product_names: Dict[str, str] = {}
        for line in lines:
            if not isinstance(line, dict):
                continue
            product_id = line.get("productId")
            if product_id is not None:
                product_id = str(product_id)
            raw_quantity = line.get("quantity")
            quantity = raw_quantity if isinstance(raw_quantity, dict) else {}
            try:
                ordered_quantity = float(quantity.get("standardQuantity", 0) or 0)
            except (TypeError, ValueError):
                ordered_quantity = 0

            if product_id and ordered_quantity > 0:
                required[product_id] = required.get(product_id, 0) + ordered_quantity
                if product_id not in product_names:
                    raw_product = line.get("product")
                    product = raw_product if isinstance(raw_product, dict) else {}
                    product_names[product_id] = str(
                        line.get("description") or product.get("name") or product_id
                    )

        shipped: Dict[str, float] = {}
        for line in pack_lines:
            if not isinstance(line, dict):
                continue
            product_id = line.get("productId")
            if product_id is not None:
                product_id = str(product_id)
            raw_quantity = line.get("quantity")
            quantity = raw_quantity if isinstance(raw_quantity, dict) else {}
            try:
                shipped_quantity = float(quantity.get("standardQuantity", 0) or 0)
            except (TypeError, ValueError):
                shipped_quantity = 0

            if product_id and shipped_quantity > 0:
                shipped[product_id] = shipped.get(product_id, 0) + shipped_quantity

        remaining_items = []
        for product_id, ordered_quantity in required.items():
            shipped_quantity = shipped.get(product_id, 0)
            remaining_quantity = ordered_quantity - shipped_quantity
            if remaining_quantity > 0.0001:
                remaining_items.append(
                    {
                        "product_id": product_id,
                        "product_name": product_names.get(product_id, product_id),
                        "ordered": int(ordered_quantity),
                        "shipped": int(shipped_quantity),
                        "remaining": int(remaining_quantity),
                    }
                )

        return {
            "is_fully_shipped": len(remaining_items) == 0,
            "total_ordered": int(sum(required.values())),
            "total_shipped": int(
                sum(
                    min(shipped.get(product_id, 0), ordered_quantity)
                    for product_id, ordered_quantity in required.items()
                )
            ),
            "remaining_items": remaining_items,
        }

    def _record_status_history(
        self,
        *,
        order: Order,
        from_status: Optional[str],
        to_status: str,
        actor_user_id: Optional[str],
        metadata: Optional[Dict[str, Any]] = None,
        changed_at: Optional[datetime] = None,
    ) -> None:
        self.db.add(
            OrderStatusHistory(
                order_id=order.id,
                from_status=from_status,
                to_status=to_status,
                changed_at=changed_at or datetime.utcnow(),
                actor_user_id=actor_user_id,
                status_metadata=metadata,
            )
        )

    def _requeue_partially_delivered_orders(
        self,
        orders: List[Order],
        user_id: Optional[str],
        audit_service: AuditService,
    ) -> Dict[str, Any]:
        requeued_orders: List[Dict[str, Any]] = []

        for order in orders:
            shipment_status = self._get_shipment_status(
                order.inflow_data if isinstance(order.inflow_data, dict) else None
            )
            if shipment_status.get("is_fully_shipped", True):
                continue

            previous_status = str(order.status) if order.status is not None else None
            reason = "Order partially delivered; remaining items returned to Picked for a new prep cycle"
            changed_at = datetime.utcnow()

            order.status = OrderStatus.PICKED.value
            order.assigned_deliverer = None
            order.delivery_run_id = None
            order.delivery_sequence = None
            order.tagged_at = None
            order.tagged_by = None
            order.tag_data = None
            order.picklist_generated_at = None
            order.picklist_generated_by = None
            order.picklist_path = None
            order.qa_completed_at = None
            order.qa_completed_by = None
            order.qa_data = None
            order.qa_path = None
            order.qa_method = None
            order.signature_captured_at = None
            order.signed_picklist_path = None
            order.order_details_path = None
            order.order_details_generated_at = None
            order.updated_at = changed_at

            self.db.add(
                AuditLog(
                    order_id=order.id,
                    changed_by=user_id or "system",
                    from_status=previous_status,
                    to_status=OrderStatus.PICKED.value,
                    reason=reason,
                    timestamp=changed_at,
                    extra_metadata={
                        "remaining_items": shipment_status.get("remaining_items", []),
                        "total_ordered": shipment_status.get("total_ordered", 0),
                        "total_shipped": shipment_status.get("total_shipped", 0),
                        "prep_reset": True,
                    },
                )
            )
            self._record_status_history(
                order=order,
                from_status=previous_status,
                to_status=OrderStatus.PICKED.value,
                actor_user_id=user_id,
                metadata={
                    "reason": reason,
                    "remaining_items": shipment_status.get("remaining_items", []),
                    "total_ordered": shipment_status.get("total_ordered", 0),
                    "total_shipped": shipment_status.get("total_shipped", 0),
                    "prep_reset": True,
                },
                changed_at=changed_at,
            )
            audit_service.log_order_action(
                order_id=str(order.id),
                action="requeued_after_partial_delivery",
                user_id=user_id,
                description=reason,
                old_value={"status": previous_status},
                new_value={"status": OrderStatus.PICKED.value},
                audit_metadata={
                    "inflow_order_id": order.inflow_order_id,
                    "remaining_items": shipment_status.get("remaining_items", []),
                    "total_ordered": shipment_status.get("total_ordered", 0),
                    "total_shipped": shipment_status.get("total_shipped", 0),
                    "prep_reset": True,
                },
            )
            requeued_orders.append(
                {
                    "order_id": str(order.id),
                    "inflow_order_id": order.inflow_order_id,
                    "status": OrderStatus.PICKED.value,
                    "remaining_items": shipment_status.get("remaining_items", []),
                    "total_ordered": shipment_status.get("total_ordered", 0),
                    "total_shipped": shipment_status.get("total_shipped", 0),
                }
            )

        return {
            "requeued_count": len(requeued_orders),
            "orders_requeued": requeued_orders,
        }

    def generate_run_name(self, run_time: datetime) -> str:
        """Generate a run name based on time and existing runs that day."""
        from app.utils.timezone import get_cst_datetime

        cst_time = get_cst_datetime(run_time)
        date_str = cst_time.strftime("%Y-%m-%d")
        is_morning = cst_time.hour < 12

        # Create datetime objects for the day boundaries in UTC
        # We need to convert CST boundaries back to UTC for database queries
        cst_tz_offset = timedelta(hours=-6)  # CST is UTC-6

        if is_morning:
            # Morning: 00:00 to 11:59 CST
            morning_start_cst = datetime.strptime(
                f"{date_str} 00:00:00", "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone(cst_tz_offset))
            morning_end_cst = datetime.strptime(
                f"{date_str} 11:59:59", "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone(cst_tz_offset))

            # Convert to UTC for database query
            morning_start_utc = morning_start_cst.astimezone(timezone.utc)
            morning_end_utc = morning_end_cst.astimezone(timezone.utc)

            existing_runs = (
                self.db.query(DeliveryRun)
                .filter(
                    and_(
                        DeliveryRun.created_at >= morning_start_utc,
                        DeliveryRun.created_at < run_time.replace(tzinfo=timezone.utc),
                    )
                )
                .count()
            )
            run_number = existing_runs + 1
            return f"Morning Run {run_number}"
        else:
            # Afternoon: 12:00 CST onwards
            afternoon_start_cst = datetime.strptime(
                f"{date_str} 12:00:00", "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone(cst_tz_offset))
            day_end_cst = datetime.strptime(
                f"{date_str} 23:59:59", "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone(cst_tz_offset))

            # Convert to UTC for database query
            afternoon_start_utc = afternoon_start_cst.astimezone(timezone.utc)
            day_end_utc = day_end_cst.astimezone(timezone.utc)

            existing_runs = (
                self.db.query(DeliveryRun)
                .filter(
                    and_(
                        DeliveryRun.created_at >= afternoon_start_utc,
                        DeliveryRun.created_at < run_time.replace(tzinfo=timezone.utc),
                    )
                )
                .count()
            )
            run_number = existing_runs + 1
            return f"Afternoon Run {run_number}"

    def check_vehicle_availability(self, vehicle: str) -> bool:
        """Return True if vehicle is available (no active run using it)"""
        active = (
            self.db.query(DeliveryRun)
            .filter(
                and_(
                    DeliveryRun.vehicle == vehicle,
                    DeliveryRun.status == DeliveryRunStatus.ACTIVE.value,
                )
            )
            .first()
        )
        return active is None

    def _validate_vehicle(self, vehicle: str) -> str:
        allowed = {v.value for v in VehicleEnum}
        vehicle_norm = (vehicle or "").strip()
        if vehicle_norm not in allowed:
            raise ValidationError(
                f"Vehicle must be one of: {sorted(allowed)}",
                field="vehicle",
                details={"allowed": sorted(allowed), "provided": vehicle},
            )
        return vehicle_norm

    def _get_authenticated_actor(self) -> tuple[str, str, str]:
        user_id = (getattr(g, "user_id", None) or "").strip()
        if not user_id:
            raise ValidationError("Authentication required")

        user_data = getattr(g, "user_data", None) or {}
        display_name = (user_data.get("display_name") or "").strip()

        if not display_name:
            legacy_user = getattr(g, "user", None)
            if legacy_user is not None:
                display_name = (
                    (getattr(legacy_user, "display_name", None) or "").strip()
                )

        if not display_name:
            with get_db() as db:
                user = db.query(User).filter(User.id == user_id).first()
                if user:
                    display_name = (user.display_name or "").strip()

        if not display_name:
            raise ValidationError("Authenticated user missing display name")

        email = (getattr(g, "user_email", None) or "").strip()
        if not email:
            legacy_user = getattr(g, "user", None)
            if legacy_user is not None:
                email = (getattr(legacy_user, "email", None) or "").strip()

        return user_id, display_name, email

    def _format_actor_display(self, display_name: str) -> str:
        return (display_name or "").strip()

    def _get_active_checkout(self, vehicle: str) -> VehicleCheckout | None:
        return (
            self.db.query(VehicleCheckout)
            .filter(
                and_(
                    VehicleCheckout.vehicle == vehicle,
                    VehicleCheckout.checked_in_at.is_(None),
                )
            )
            .first()
        )

    def create_run_for_current_user(
        self, order_ids: Sequence[Union[UUID, str]], vehicle: str
    ) -> DeliveryRun:
        vehicle_norm = self._validate_vehicle(vehicle)
        actor_user_id, actor_display_name, actor_email = self._get_authenticated_actor()
        runner_display = self._format_actor_display(actor_display_name)

        active_checkout = self._get_active_checkout(vehicle_norm)
        if not active_checkout:
            raise ValidationError(
                f"Vehicle {vehicle_norm} must be checked out before starting a delivery run",
                field="vehicle",
                details={
                    "vehicle": vehicle_norm,
                    "required_checkout_type": "delivery_run",
                },
            )

        if (active_checkout.checked_out_by_user_id or "").strip() != actor_user_id:
            raise ValidationError(
                f"Vehicle {vehicle_norm} is checked out by a different user",
                field="runner",
                details={
                    "vehicle": vehicle_norm,
                    "checked_out_by": active_checkout.checked_out_by,
                },
            )

        checkout_type = (
            getattr(active_checkout, "checkout_type", "") or ""
        ).strip() or "delivery_run"
        if checkout_type != "delivery_run":
            purpose = (active_checkout.purpose or "").strip() or None
            reason = "Vehicle is checked out for 'Other'"
            if purpose:
                reason += f" (purpose: {purpose})"

            raise ValidationError(
                f"{reason}. Check the vehicle in, then check it out again for a Delivery run.",
                field="checkout_type",
                details={
                    "vehicle": vehicle_norm,
                    "checkout_type": checkout_type,
                    "purpose": purpose,
                    "required_checkout_type": "delivery_run",
                },
            )

        return self.create_run(
            runner=runner_display, order_ids=order_ids, vehicle=vehicle_norm
        )

    def create_run(
        self, runner: str, order_ids: Sequence[Union[UUID, str]], vehicle: str
    ) -> DeliveryRun:
        """Create a delivery run and assign orders to it.

        Validates that orders are in Pre-Delivery and that the vehicle is available.
        """
        vehicle_norm = self._validate_vehicle(vehicle)

        # Vehicle availability — lock existing active runs for this vehicle to prevent races
        existing_run = (
            self.db.query(DeliveryRun)
            .filter(
                DeliveryRun.vehicle == vehicle_norm,
                DeliveryRun.status == DeliveryRunStatus.ACTIVE.value,
            )
            .with_for_update()
            .first()
        )
        if existing_run is not None:
            raise ValidationError(
                f"Vehicle {vehicle} is currently in use",
                details={"vehicle": vehicle, "active_run_id": str(existing_run.id)},
            )

        # Convert order IDs to strings for MySQL compatibility
        order_ids_str = [str(oid) for oid in order_ids]
        order_position_map = {
            order_id: index + 1 for index, order_id in enumerate(order_ids_str)
        }

        # Validate orders
        orders = (
            self.db.query(Order)
            .filter(Order.id.in_(order_ids_str))
            .with_for_update()
            .all()
        )
        if len(orders) != len(order_ids_str):
            raise ValidationError(
                "One or more orders not found",
                details={"expected_count": len(order_ids), "found_count": len(orders)},
            )

        for o in orders:
            if o.status != OrderStatus.PRE_DELIVERY.value:
                raise ValidationError(
                    f"Order {o.inflow_order_id or o.id} not in Pre-Delivery",
                    details={"order_id": str(o.id), "current_status": o.status},
                )

        # Generate run name
        run_time = datetime.utcnow()
        run_name = self.generate_run_name(run_time)

        # Create run
        run = DeliveryRun(
            name=run_name,
            runner=runner,
            vehicle=vehicle_norm,
            status=DeliveryRunStatus.ACTIVE.value,
            start_time=run_time,
        )
        self.db.add(run)
        self.db.flush()  # ensure run.id available

        # Assign orders and create audit logs for status changes
        for o in orders:
            old_status = o.status
            o.delivery_run_id = run.id
            o.delivery_sequence = order_position_map.get(str(o.id))
            o.status = OrderStatus.IN_DELIVERY.value
            o.updated_at = datetime.utcnow()

            # Create AuditLog entry for timeline display
            audit_log = AuditLog(
                order_id=o.id,
                changed_by=runner,
                from_status=old_status,
                to_status=OrderStatus.IN_DELIVERY.value,
                reason=f"Added to delivery run: {run_name}",
                timestamp=datetime.utcnow(),
            )
            self.db.add(audit_log)

        self.db.commit()
        self.db.refresh(run)

        # Also log to system audit log for full traceability
        audit_service = AuditService(self.db)
        audit_service.log_delivery_run_action(
            run_id=str(run.id),
            action="created",
            user_id=runner,  # Runner who created the run
            description=f"Delivery run created by {runner}",
            audit_metadata={
                "vehicle": vehicle_norm,
                "order_count": len(order_ids),
                "order_ids": order_ids_str,
                "run_name": run_name,
            },
        )

        return run

    def get_run_by_id(self, run_id: Union[UUID, str]) -> DeliveryRun | None:
        run_id_str = str(run_id)
        return self.db.query(DeliveryRun).filter(DeliveryRun.id == run_id_str).first()

    def get_active_runs_with_details(self) -> List[DeliveryRun]:
        return (
            self.db.query(DeliveryRun)
            .filter(DeliveryRun.status == DeliveryRunStatus.ACTIVE.value)
            .all()
        )

    def get_all_run_details(
        self, status: Optional[List[str]] = None, vehicle: Optional[str] = None
    ) -> List[DeliveryRun]:
        """Get all delivery runs, optionally filtered by status/vehicle."""
        query = self.db.query(DeliveryRun)

        if status:
            query = query.filter(DeliveryRun.status.in_(status))

        if vehicle is not None:
            vehicle_norm = self._validate_vehicle(vehicle)
            query = query.filter(DeliveryRun.vehicle == vehicle_norm)

        return query.order_by(DeliveryRun.created_at.desc()).all()

    def _fulfill_orders_in_inflow(
        self, orders: List[Order], user_id: Optional[str]
    ) -> tuple[List[dict], List[dict]]:
        if not orders:
            return [], []

        inflow_service = InflowService()

        async def _fulfill() -> tuple[List[dict], List[dict]]:
            successes: List[dict] = []
            failures: List[dict] = []

            async def _fulfill_one(order):
                inflow_sales_order_id = order.inflow_sales_order_id
                if not inflow_sales_order_id:
                    return None, {
                        "order_id": str(order.id),
                        "inflow_order_id": order.inflow_order_id,
                        "error": "missing_inflow_sales_order_id",
                    }
                try:
                    updated_inflow_order = await inflow_service.fulfill_sales_order(
                        inflow_sales_order_id,
                        db=self.db,
                        user_id=user_id,
                        only_picked_items=True,
                    )
                    order.inflow_data = updated_inflow_order
                    return {
                        "order_id": str(order.id),
                        "inflow_order_id": order.inflow_order_id,
                        "inflow_sales_order_id": inflow_sales_order_id,
                    }, None
                except Exception as exc:
                    return None, {
                        "order_id": str(order.id),
                        "inflow_order_id": order.inflow_order_id,
                        "inflow_sales_order_id": inflow_sales_order_id,
                        "error": str(exc),
                    }

            results = await asyncio.gather(*[_fulfill_one(o) for o in orders])
            successes.extend(s for s, _ in results if s is not None)
            failures.extend(f for _, f in results if f is not None)

            return successes, failures

        return asyncio.run(_fulfill())

    def finish_run(
        self,
        run_id: Union[UUID, str],
        user_id: Optional[str] = None,
        create_remainders: bool = True,
        expected_updated_at: Optional[datetime] = None,
    ) -> DeliveryRun:
        """
        Finish a delivery run by fulfilling delivered orders in InFlow and marking the run complete.

        Splits into three phases to avoid holding the DB lock during network I/O:
          1. Validate (read-only, no lock)
          2. Fulfill in InFlow (no DB lock held)
          3. Short locking transaction for final status + commit
        """
        run_id_str = str(run_id)

        # Phase 1: validate state (read-only, no long-lived lock)
        run = self.db.query(DeliveryRun).filter(DeliveryRun.id == run_id_str).first()
        if not run:
            raise NotFoundError("DeliveryRun", str(run_id))

        if run.status != DeliveryRunStatus.ACTIVE.value:
            raise ValidationError(
                "Cannot finish delivery run because it is not active",
                details={"run_id": run_id_str, "current_status": run.status},
            )

        if expected_updated_at is not None and run.updated_at is not None:
            expected_utc = self._normalize_stale_timestamp(expected_updated_at)
            current_utc = self._normalize_stale_timestamp(run.updated_at)

            if expected_utc != current_utc:
                raise ConflictError(
                    "Delivery run has changed since it was loaded. Refresh and try again.",
                    details={
                        "run_id": run_id_str,
                        "expected_updated_at": to_utc_iso_z(expected_utc),
                        "current_updated_at": to_utc_iso_z(current_utc),
                    },
                )

        # Validate ALL orders are already delivered
        undelivered_orders = [
            o for o in run.orders if o.status != OrderStatus.DELIVERED.value
        ]
        if undelivered_orders:
            raise ValidationError(
                f"Cannot finish delivery run: {len(undelivered_orders)} orders are not yet delivered",
                details={
                    "undelivered_count": len(undelivered_orders),
                    "undelivered_order_ids": [str(o.id) for o in undelivered_orders],
                },
            )

        # Phase 2: fulfill in InFlow (no DB lock held during network I/O)
        inflow_successes, inflow_failures = self._fulfill_orders_in_inflow(
            run.orders, user_id
        )

        if inflow_failures:
            # Log failure in a quick write
            audit_service = AuditService(self.db)
            audit_service.log_delivery_run_action(
                run_id=run_id_str,
                action="completion_failed",
                user_id=user_id,
                description="Delivery run completion failed during inFlow fulfillment",
                audit_metadata={
                    "order_count": len(run.orders),
                    "fulfilled_orders": inflow_successes,
                    "failed_orders": inflow_failures,
                },
            )
            self.db.commit()
            raise ValidationError(
                "Cannot finish delivery run: inFlow fulfillment failed for one or more orders",
                details={
                    "failed_orders": inflow_failures,
                    "fulfilled_count": len(inflow_successes),
                },
            )

        # Phase 3: short locking transaction for final status update
        # ``create_remainders`` is retained for API compatibility but no longer
        # requeues local partial legs. Those are already split into separate
        # local orders and should remain delivered once the run finishes.
        audit_service = AuditService(self.db)

        run = (
            self.db.query(DeliveryRun)
            .filter(DeliveryRun.id == run_id_str)
            .with_for_update()
            .first()
        )
        # Re-check status after acquiring lock
        if run.status != DeliveryRunStatus.ACTIVE.value:
            raise ValidationError(
                "Delivery run status changed during completion",
                details={"run_id": run_id_str, "current_status": run.status},
            )

        run.status = DeliveryRunStatus.COMPLETED.value
        run.end_time = datetime.utcnow()
        run.updated_at = datetime.utcnow()

        audit_service.log_delivery_run_action(
            run_id=run_id_str,
            action="completed",
            user_id=user_id,
            description="Delivery run completed",
            audit_metadata={
                "order_count": len(inflow_successes),
                "completed_at": to_utc_iso_z(run.end_time),
                "fulfilled_orders": inflow_successes,
                "create_remainders_requested": create_remainders,
                "partial_orders_requeued": 0,
                "requeued_orders": [],
            },
        )

        self.db.commit()
        self.db.refresh(run)

        return run

    def recall_order_from_run(
        self,
        run_id: Union[UUID, str],
        order_id: Union[UUID, str],
        reason: str,
        expected_updated_at: Optional[datetime] = None,
    ) -> DeliveryRun:
        """Recall an undeliverable order from an active run, moving it to ISSUE and detaching it."""
        reason_value = (reason or "").strip()
        if not reason_value:
            raise ValidationError("Recall reason is required", field="reason")

        actor_user_id, actor_display_name, actor_email = self._get_authenticated_actor()
        actor = self._format_actor_display(actor_display_name)

        run_id_str = str(run_id)
        order_id_str = str(order_id)

        run = (
            self.db.query(DeliveryRun)
            .filter(DeliveryRun.id == run_id_str)
            .with_for_update()
            .first()
        )
        if not run:
            raise NotFoundError("DeliveryRun", run_id_str)

        if run.status != DeliveryRunStatus.ACTIVE.value:
            raise ValidationError(
                "Only active runs support order recall",
                details={"run_id": run_id_str, "current_status": run.status},
            )

        if expected_updated_at is not None and run.updated_at is not None:
            expected_utc = self._normalize_stale_timestamp(expected_updated_at)
            current_utc = self._normalize_stale_timestamp(run.updated_at)

            if expected_utc != current_utc:
                raise ConflictError(
                    "Delivery run has changed since it was loaded. Refresh and try again.",
                    details={
                        "run_id": run_id_str,
                        "expected_updated_at": to_utc_iso_z(expected_utc),
                        "current_updated_at": to_utc_iso_z(current_utc),
                    },
                )

        order = (
            self.db.query(Order)
            .filter(Order.id == order_id_str)
            .with_for_update()
            .first()
        )
        if not order:
            raise NotFoundError("Order", order_id_str)

        if str(order.delivery_run_id) != run_id_str:
            raise ValidationError(
                "Order is not assigned to this run",
                details={
                    "order_id": order_id_str,
                    "delivery_run_id": order.delivery_run_id,
                    "run_id": run_id_str,
                },
            )

        if order.status == OrderStatus.DELIVERED.value:
            raise ValidationError(
                "Delivered orders cannot be recalled",
                details={"order_id": order_id_str, "status": order.status},
            )

        previous_status = order.status
        order.status = OrderStatus.ISSUE.value
        order.issue_reason = reason_value
        order.delivery_run_id = None
        order.updated_at = datetime.utcnow()
        run.updated_at = datetime.utcnow()

        self.db.add(
            AuditLog(
                order_id=order.id,
                changed_by=actor,
                from_status=previous_status,
                to_status=OrderStatus.ISSUE.value,
                reason=f"Recalled from run {run.name}: {reason_value}",
            )
        )

        audit_service = AuditService(self.db)
        audit_service.log_delivery_run_action(
            run_id=run_id_str,
            action="order_recalled",
            user_id=actor_user_id,
            description=f"Order {order.inflow_order_id} recalled from active run",
            audit_metadata={
                "order_id": order_id_str,
                "order_inflow_id": order.inflow_order_id,
                "reason": reason_value,
                "previous_status": previous_status,
                "new_status": OrderStatus.ISSUE.value,
            },
        )

        audit_service.log_order_action(
            order_id=order_id_str,
            action="recalled_from_delivery_run",
            user_id=actor_user_id,
            description=f"Order recalled from run {run.name}",
            old_value={"status": previous_status, "delivery_run_id": run_id_str},
            new_value={"status": OrderStatus.ISSUE.value, "delivery_run_id": None},
            audit_metadata={"reason": reason_value},
        )

        self.db.commit()
        self.db.refresh(run)
        return run

    def reorder_run_orders(
        self,
        run_id: Union[UUID, str],
        order_ids: Sequence[Union[UUID, str]],
        expected_updated_at: Optional[datetime] = None,
    ) -> DeliveryRun:
        """Persist sequence ordering for orders currently assigned to an active run."""
        run_id_str = str(run_id)
        requested_order_ids = [str(order_id) for order_id in order_ids]

        if not requested_order_ids:
            raise ValidationError("order_ids is required", field="order_ids")

        run = (
            self.db.query(DeliveryRun)
            .filter(DeliveryRun.id == run_id_str)
            .with_for_update()
            .first()
        )
        if not run:
            raise NotFoundError("DeliveryRun", run_id_str)

        if run.status != DeliveryRunStatus.ACTIVE.value:
            raise ValidationError(
                "Only active runs can be reordered",
                details={"run_id": run_id_str, "current_status": run.status},
            )

        if expected_updated_at is not None and run.updated_at is not None:
            expected_utc = self._normalize_stale_timestamp(expected_updated_at)
            current_utc = self._normalize_stale_timestamp(run.updated_at)

            if expected_utc != current_utc:
                raise ConflictError(
                    "Delivery run has changed since it was loaded. Refresh and try again.",
                    details={
                        "run_id": run_id_str,
                        "expected_updated_at": to_utc_iso_z(expected_utc),
                        "current_updated_at": to_utc_iso_z(current_utc),
                    },
                )

        run_orders = (
            self.db.query(Order)
            .filter(Order.delivery_run_id == run_id_str)
            .with_for_update()
            .all()
        )
        run_order_ids = {str(order.id) for order in run_orders}
        requested_set = set(requested_order_ids)

        if requested_set != run_order_ids or len(requested_order_ids) != len(
            run_order_ids
        ):
            raise ValidationError(
                "order_ids must include all and only orders assigned to the run",
                details={
                    "run_order_ids": sorted(run_order_ids),
                    "requested_order_ids": requested_order_ids,
                },
            )

        order_lookup = {str(order.id): order for order in run_orders}
        for index, order_id in enumerate(requested_order_ids, start=1):
            order = order_lookup[order_id]
            order.delivery_sequence = index
            order.updated_at = datetime.utcnow()

        run.updated_at = datetime.utcnow()

        actor_user_id, _, _ = self._get_authenticated_actor()
        audit_service = AuditService(self.db)
        audit_service.log_delivery_run_action(
            run_id=run_id_str,
            action="orders_reordered",
            user_id=actor_user_id,
            description="Run order sequence updated",
            audit_metadata={"order_ids": requested_order_ids},
        )

        self.db.commit()
        self.db.refresh(run)
        return run
