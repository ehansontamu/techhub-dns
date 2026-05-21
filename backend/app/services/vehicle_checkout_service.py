from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import logging
from typing import Optional

from flask import g
from sqlalchemy import and_, text
from sqlalchemy.orm import Session

from app.models.delivery_run import DeliveryRun, DeliveryRunStatus, VehicleEnum
from app.models.vehicle_checkout import VehicleCheckout
from app.utils.exceptions import ValidationError
from app.utils.timezone import to_utc_iso_z

logger = logging.getLogger(__name__)


class VehicleCheckoutService:
    def __init__(self, db: Session):
        self.db = db

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

    def _delivery_run_active(self, vehicle: str) -> bool:
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
        return active is not None

    def get_active_checkout(self, vehicle: str) -> Optional[VehicleCheckout]:
        vehicle_norm = self._validate_vehicle(vehicle)
        return (
            self.db.query(VehicleCheckout)
            .filter(
                and_(
                    VehicleCheckout.vehicle == vehicle_norm,
                    VehicleCheckout.checked_in_at.is_(None),
                )
            )
            .first()
        )

    def get_active_checkouts(self) -> list[VehicleCheckout]:
        return (
            self.db.query(VehicleCheckout)
            .filter(VehicleCheckout.checked_in_at.is_(None))
            .all()
        )

    def _get_authenticated_actor(self) -> tuple[str, str, Optional[str]]:
        user_id = (getattr(g, "user_id", None) or "").strip()
        if not user_id:
            raise ValidationError("Authentication required")

        email = (getattr(g, "user_email", None) or "").strip()
        user_data = getattr(g, "user_data", None) or {}
        display_name = (user_data.get("display_name") or "").strip()

        if not display_name:
            legacy_user = getattr(g, "user", None)
            legacy_display_name = (
                getattr(legacy_user, "display_name", None) if legacy_user else None
            )
            display_name = (legacy_display_name or "").strip()

        if not display_name:
            raise ValidationError("Authenticated user missing display name")

        if not email:
            legacy_user = getattr(g, "user", None)
            legacy_email = getattr(legacy_user, "email", None) if legacy_user else None
            email = (legacy_email or "").strip()

        return user_id, email, display_name

    def _format_actor_display(self, display_name: str) -> str:
        return (display_name or "").strip()

    @contextmanager
    def _vehicle_lock(self, vehicle: str):
        bind = self.db.get_bind()
        if bind.dialect.name != "mysql":
            yield
            return

        lock_name = f"vehicle-checkout:{vehicle}"

        # Use a dedicated raw connection for the advisory lock so it's released
        # automatically when the connection is closed — no session-state risk.
        lock_conn = bind.connect()
        try:
            acquired = lock_conn.execute(
                text("SELECT GET_LOCK(:lock_name, :timeout_seconds)"),
                {"lock_name": lock_name, "timeout_seconds": 10},
            ).scalar()
            if acquired != 1:
                logger.warning(
                    "Timed out waiting for vehicle checkout lock: vehicle=%s", vehicle
                )
                raise ValidationError(
                    f"Vehicle {vehicle} is busy processing another request. Please try again.",
                    details={"vehicle": vehicle, "lock_timeout_seconds": 10},
                )

            try:
                yield
            finally:
                try:
                    lock_conn.execute(
                        text("SELECT RELEASE_LOCK(:lock_name)"), {"lock_name": lock_name}
                    )
                except Exception:
                    logger.warning(
                        "Failed to release vehicle lock: %s", lock_name, exc_info=True
                    )
        finally:
            # Closing the connection releases any remaining lock as a safety net
            try:
                lock_conn.close()
            except Exception:
                pass

    def recover_stale_vehicle_locks(self, max_lock_age_minutes: int = 10) -> int:
        """Release any vehicle locks that have been held longer than max_lock_age_minutes."""
        stale = self.db.query(VehicleCheckout).filter(
            VehicleCheckout.checked_in_at.is_(None),
            VehicleCheckout.created_at < datetime.utcnow() - timedelta(minutes=max_lock_age_minutes)
        ).all()
        count = 0
        for checkout in stale:
            checkout.checked_in_at = datetime.utcnow()
            count += 1
        if count:
            self.db.commit()
            logger.info("Recovered %d stale vehicle checkouts", count)
        return count

    def checkout(
        self,
        vehicle: str,
        checkout_type: str = "delivery_run",
        purpose: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> VehicleCheckout:
        vehicle_norm = self._validate_vehicle(vehicle)
        actor_user_id, actor_email, actor_display_name = self._get_authenticated_actor()
        checked_out_by_display = self._format_actor_display(actor_display_name)

        checkout_type_norm = (checkout_type or "").strip()
        if checkout_type_norm not in {"delivery_run", "other"}:
            raise ValidationError(
                "checkout_type must be 'delivery_run' or 'other'",
                field="checkout_type",
                details={"provided": checkout_type},
            )

        purpose_norm = (purpose or "").strip() or None
        if checkout_type_norm == "other" and not purpose_norm:
            raise ValidationError(
                "Purpose is required when checking out with type 'Other'",
                field="purpose",
                details={"checkout_type": checkout_type_norm},
            )

        with self._vehicle_lock(vehicle_norm):
            if self._delivery_run_active(vehicle_norm):
                raise ValidationError(
                    f"Vehicle {vehicle_norm} is currently in use",
                    details={"vehicle": vehicle_norm, "delivery_run_active": True},
                )

            existing = (
                self.db.query(VehicleCheckout)
                .filter(
                    and_(
                        VehicleCheckout.vehicle == vehicle_norm,
                        VehicleCheckout.checked_in_at.is_(None),
                    )
                )
                .first()
            )
            if existing:
                raise ValidationError(
                    f"Vehicle {vehicle_norm} is already checked out",
                    details={
                        "vehicle": vehicle_norm,
                        "checked_out_by": existing.checked_out_by,
                        "checked_out_at": to_utc_iso_z(existing.checked_out_at),
                    },
                )

            checkout = VehicleCheckout(
                vehicle=vehicle_norm,
                checked_out_by=checked_out_by_display,
                checked_out_by_user_id=actor_user_id,
                checked_out_by_email=actor_email,
                checked_out_by_display_name=actor_display_name,
                checkout_type=checkout_type_norm,
                purpose=purpose_norm,
                notes=(notes.strip() if notes else None) or None,
                checked_out_at=datetime.utcnow(),
            )
            self.db.add(checkout)
            self.db.commit()
            self.db.refresh(checkout)
            logger.info(
                "Vehicle checked out: vehicle=%s checkout_id=%s actor=%s checkout_type=%s",
                vehicle_norm,
                checkout.id,
                actor_email,
                checkout_type_norm,
            )
            return checkout

    def checkin(
        self,
        vehicle: str,
        notes: Optional[str] = None,
        allow_active_delivery_run: bool = False,
    ) -> VehicleCheckout:
        vehicle_norm = self._validate_vehicle(vehicle)
        actor_user_id, actor_email, actor_display_name = self._get_authenticated_actor()
        checked_in_by_display = self._format_actor_display(actor_display_name)
        with self._vehicle_lock(vehicle_norm):
            if self._delivery_run_active(vehicle_norm) and not allow_active_delivery_run:
                raise ValidationError(
                    f"Cannot check in {vehicle_norm} while a delivery run is active",
                    details={"vehicle": vehicle_norm, "delivery_run_active": True},
                )

            checkout = (
                self.db.query(VehicleCheckout)
                .filter(
                    and_(
                        VehicleCheckout.vehicle == vehicle_norm,
                        VehicleCheckout.checked_in_at.is_(None),
                    )
                )
                .first()
            )
            if not checkout:
                raise ValidationError(
                    f"Vehicle {vehicle_norm} is not currently checked out",
                    details={"vehicle": vehicle_norm},
                )

            notes_norm = (notes or "").strip() or None
            if notes_norm:
                if checkout.notes:
                    checkout.notes = f"{checkout.notes}\n\n[Checkin note] {notes_norm}"
                else:
                    checkout.notes = f"[Checkin note] {notes_norm}"

            checkout.checked_in_at = datetime.utcnow()
            checkout.checked_in_by = checked_in_by_display
            checkout.checked_in_by_user_id = actor_user_id
            checkout.checked_in_by_email = actor_email
            checkout.checked_in_by_display_name = actor_display_name
            checkout.updated_at = datetime.utcnow()

            self.db.commit()
            self.db.refresh(checkout)
            logger.info(
                "Vehicle checked in: vehicle=%s checkout_id=%s actor=%s",
                vehicle_norm,
                checkout.id,
                actor_email,
            )
            return checkout

    def get_vehicle_statuses(self) -> list[dict]:
        statuses: list[dict] = []
        for v in VehicleEnum:
            vehicle = v.value
            active_checkout = (
                self.db.query(VehicleCheckout)
                .filter(
                    and_(
                        VehicleCheckout.vehicle == vehicle,
                        VehicleCheckout.checked_in_at.is_(None),
                    )
                )
                .first()
            )
            statuses.append(
                {
                    "vehicle": vehicle,
                    "checked_out": active_checkout is not None,
                    "checked_out_by": (
                        (active_checkout.checked_out_by_display_name or active_checkout.checked_out_by or None)
                        if active_checkout
                        else None
                    ),
                    "checked_out_by_user_id": active_checkout.checked_out_by_user_id
                    if active_checkout
                    else None,
                    "checkout_type": getattr(active_checkout, "checkout_type", None)
                    if active_checkout
                    else None,
                    "purpose": active_checkout.purpose if active_checkout else None,
                    "checked_out_at": active_checkout.checked_out_at
                    if active_checkout
                    else None,
                    "delivery_run_active": self._delivery_run_active(vehicle),
                }
            )
        return statuses

    def list_checkouts(
        self,
        vehicle: Optional[str] = None,
        checkout_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 25,
    ) -> dict:
        if page < 1:
            raise ValidationError("page must be >= 1", field="page")
        if page_size < 1 or page_size > 200:
            raise ValidationError(
                "page_size must be between 1 and 200", field="page_size"
            )

        query = self.db.query(VehicleCheckout)

        if vehicle is not None:
            vehicle_norm = self._validate_vehicle(vehicle)
            query = query.filter(VehicleCheckout.vehicle == vehicle_norm)

        if checkout_type is not None:
            checkout_type_norm = (checkout_type or "").strip()
            if checkout_type_norm not in {"delivery_run", "other"}:
                raise ValidationError(
                    "checkout_type must be 'delivery_run' or 'other'",
                    field="checkout_type",
                    details={"provided": checkout_type},
                )
            query = query.filter(VehicleCheckout.checkout_type == checkout_type_norm)

        total = query.count()
        items = (
            query.order_by(VehicleCheckout.checked_out_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )

        return {
            "items": [
                {
                    **{
                        "id": str(c.id),
                        "vehicle": c.vehicle,
                        "checked_out_by": (
                            (c.checked_out_by_display_name or c.checked_out_by or "").strip()
                        ),
                        "checked_out_by_user_id": c.checked_out_by_user_id,
                        "checked_out_by_email": c.checked_out_by_email,
                        "checkout_type": c.checkout_type,
                        "purpose": c.purpose,
                        "notes": c.notes,
                        "checked_out_at": to_utc_iso_z(c.checked_out_at),
                        "checked_in_at": to_utc_iso_z(c.checked_in_at),
                        "checked_in_by": (
                            (c.checked_in_by_display_name or c.checked_in_by or "").strip()
                        ),
                    }
                }
                for c in items
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
        }
