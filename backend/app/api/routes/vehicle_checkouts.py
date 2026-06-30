from __future__ import annotations

from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request, abort
from pydantic import ValidationError as PydanticValidationError

from app.api.vehicle_status_events import broadcast_vehicle_status_update_sync
from app.api.auth_middleware import require_auth
from app.database import get_db
from app.schemas.vehicle_checkout import (
    CheckoutRequest,
    CheckinRequest,
    VehicleCheckoutResponse,
    VehicleStatusItem,
    VehicleStatusResponse,
)
from app.services.vehicle_checkout_service import VehicleCheckoutService
from app.utils.exceptions import ValidationError
from app.utils.broadcast_dedup import broadcast_dedup


vehicle_checkouts_bp = Blueprint("vehicle_checkouts", __name__, url_prefix="/api/vehicle-checkouts")
vehicles_bp = Blueprint("vehicles", __name__, url_prefix="/api/vehicles")


@vehicle_checkouts_bp.route("/checkout", methods=["POST"])
@require_auth
def checkout_vehicle():
    data = request.get_json() or {}
    try:
        req = CheckoutRequest(**data)
    except PydanticValidationError as exc:
        raise ValidationError("Invalid checkout request", details={"errors": exc.errors()})

    with get_db() as db:
        service = VehicleCheckoutService(db)
        checkout = service.checkout(
            vehicle=req.vehicle,
            checkout_type=req.checkout_type,
            purpose=req.purpose,
            notes=req.notes,
        )
        broadcast_dedup.request_broadcast(broadcast_vehicle_status_update_sync)
        response = VehicleCheckoutResponse.model_validate(checkout)
        return jsonify(response.model_dump())


@vehicle_checkouts_bp.route("/checkin", methods=["POST"])
@require_auth
def checkin_vehicle():
    data = request.get_json() or {}
    try:
        req = CheckinRequest(**data)
    except PydanticValidationError as exc:
        raise ValidationError("Invalid checkin request", details={"errors": exc.errors()})

    with get_db() as db:
        service = VehicleCheckoutService(db)
        checkout = service.checkin(vehicle=req.vehicle, notes=req.notes)
        broadcast_dedup.request_broadcast(broadcast_vehicle_status_update_sync)
        response = VehicleCheckoutResponse.model_validate(checkout)
        return jsonify(response.model_dump())


@vehicle_checkouts_bp.route("/active", methods=["GET"])
@require_auth
def get_active_checkouts():
    with get_db() as db:
        service = VehicleCheckoutService(db)
        active = service.get_active_checkouts()
        response = [VehicleCheckoutResponse.model_validate(c).model_dump() for c in active]
        return jsonify(response)


@vehicle_checkouts_bp.route("", methods=["GET"])
@require_auth
def list_vehicle_checkouts():
    """List vehicle checkout history (paged), with optional date range filter."""
    vehicle = request.args.get("vehicle")
    checkout_type = request.args.get("checkout_type")
    page = request.args.get("page", type=int) or 1
    page_size = request.args.get("page_size", type=int) or 25
    start_date_str = request.args.get("start_date")
    end_date_str = request.args.get("end_date")

    start_date = None
    end_date = None
    try:
        if start_date_str:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        if end_date_str:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        abort(400, description="Invalid date format — expected YYYY-MM-DD")

    with get_db() as db:
        service = VehicleCheckoutService(db)
        result = service.list_checkouts(
            vehicle=vehicle,
            checkout_type=checkout_type,
            start_date=start_date,
            end_date=end_date,
            page=page,
            page_size=page_size,
        )
        return jsonify(result)


@vehicles_bp.route("/status", methods=["GET"])
def get_vehicle_statuses():
    with get_db() as db:
        service = VehicleCheckoutService(db)
        items = [VehicleStatusItem(**s) for s in service.get_vehicle_statuses()]
        response = VehicleStatusResponse(vehicles=items)
        return jsonify(response.model_dump())
