from flask import Blueprint, request, jsonify, abort
from flask_socketio import emit
from typing import List
from uuid import UUID

from app.database import get_db, get_db_session
from app.api.auth_middleware import require_auth
from app.api.vehicle_status_events import broadcast_vehicle_status_update_sync
from app.services.delivery_run_service import DeliveryRunService
from app.schemas.delivery_run import (
    CreateDeliveryRunRequest,
    DeliveryRunResponse,
    FinishDeliveryRunRequest,
    RecallDeliveryRunOrderRequest,
    ReorderDeliveryRunOrdersRequest,
)
from app.models.delivery_run import DeliveryRun, VehicleEnum
from app.utils.exceptions import ValidationError
from app.utils.timezone import to_utc_iso_z
from app.utils.broadcast_dedup import broadcast_dedup
from app.utils.display_labels import resolve_runner_display
from pydantic import ValidationError as PydanticValidationError

bp = Blueprint("delivery_runs", __name__)
bp.strict_slashes = False


def _prepare_runs_payload(runs: list[DeliveryRun], db_session=None) -> list[dict]:
    payload = []
    for r in runs:
        raw_runner = (r.runner or "").strip()
        runner_label = resolve_runner_display(db_session, raw_runner) if db_session else raw_runner
        payload.append(
            {
                "id": str(r.id),
                "runner": runner_label,
                "vehicle": r.vehicle.value
                if hasattr(r.vehicle, "value")
                else str(r.vehicle),
                "status": r.status.value
                if hasattr(r.status, "value")
                else str(r.status),
                "start_time": to_utc_iso_z(r.start_time),
                "order_ids": [str(o.id) for o in r.orders],
            }
        )
    return payload


def _delivery_run_response(run, db_session) -> dict:
    """Build a DeliveryRunResponse dict with resolved runner display name."""
    raw_runner = (run.runner or "").strip()
    runner_label = resolve_runner_display(db_session, raw_runner)
    return DeliveryRunResponse(
        id=run.id,
        name=run.name,
        runner=runner_label,
        vehicle=run.vehicle,
        status=run.status,
        start_time=run.start_time,
        end_time=run.end_time,
        order_ids=[o.id for o in run.orders],
    ).model_dump(mode="json")


def _broadcast_active_runs_sync(db_session=None):
    """Send current active runs to all connected clients (sync version)."""
    if db_session is not None:
        _do_broadcast_active_runs(db_session)
        return

    from app.database import get_db

    with get_db() as db:
        _do_broadcast_active_runs(db)


def _do_broadcast_active_runs(db_session):
    try:
        service = DeliveryRunService(db_session)
        runs = service.get_active_runs_with_details()
        payload = _prepare_runs_payload(runs, db_session)

        # Emit via SocketIO to all connected clients in 'orders' room
        try:
            from app.main import socketio

            # Frontend joins 'delivery-runs' room for active run updates
            socketio.emit(
                "active_runs", {"type": "active_runs", "data": payload}, room="delivery-runs"
            )
        except Exception as e:
            import logging

            logging.getLogger(__name__).error(f"Failed to broadcast active runs: {e}")
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Failed to broadcast active runs")


@bp.route("", methods=["POST"])
@require_auth
def create_run():
    """Create a new delivery run"""
    data = request.get_json() or {}

    with get_db() as db:
        service = DeliveryRunService(db)
        try:
            req = CreateDeliveryRunRequest(**data)
        except PydanticValidationError as exc:
            raise ValidationError(
                "Invalid create run request", details={"errors": exc.errors()}
            )

        try:
            run = service.create_run_for_current_user(
                order_ids=req.order_ids, vehicle=req.vehicle
            )

            # Broadcast via SocketIO in background
            broadcast_dedup.request_broadcast(_broadcast_active_runs_sync)
            broadcast_dedup.request_broadcast(broadcast_vehicle_status_update_sync)

            # Trigger Teams notifications for orders in delivery
            try:
                from app.services.teams_recipient_service import teams_recipient_service

                teams_recipient_service.notify_orders_in_delivery(run.orders)
            except Exception as e:
                # Log but don't fail the request
                from app.api.routes.orders import logger as order_logger

                order_logger.error(
                    f"Failed to trigger Teams notifications for delivery run: {e}"
                )

            response = _delivery_run_response(run, db)
            return jsonify(response)
        except ValueError as e:
            abort(400, description=str(e))


@bp.route("", methods=["GET"])
@require_auth
def get_runs():
    """Get delivery runs (optionally filtered by status)"""
    status_filter = request.args.getlist("status")
    vehicle = request.args.get("vehicle")

    with get_db() as db:
        service = DeliveryRunService(db)
        runs = service.get_all_run_details(
            status=status_filter if status_filter else None, vehicle=vehicle
        )

        result = []
        for r in runs:
            result.append(_delivery_run_response(r, db))
        return jsonify(result)


@bp.route("/active", methods=["GET"])
@require_auth
def get_active_runs():
    """Get all active delivery runs"""
    with get_db() as db:
        service = DeliveryRunService(db)
        runs = service.get_active_runs_with_details()
        result = []
        for r in runs:
            result.append(_delivery_run_response(r, db))
        return jsonify(result)


@bp.route("/vehicles/available", methods=["GET"])
@require_auth
def get_available_vehicles():
    """Get available vehicles"""
    with get_db() as db:
        service = DeliveryRunService(db)
        vehicles = {
            v.value: service.check_vehicle_availability(v.value) for v in VehicleEnum
        }
        return jsonify(vehicles)


@bp.route("/<uuid:run_id>", methods=["GET"])
@require_auth
def get_run(run_id):
    """Get delivery run details"""
    with get_db() as db:
        service = DeliveryRunService(db)
        run = service.get_run_by_id(run_id)
        if not run:
            abort(404, description="Delivery run not found")

        from app.schemas.delivery_run import DeliveryRunDetailResponse, OrderSummary

        raw_runner = (run.runner or "").strip()
        runner_label = resolve_runner_display(db, raw_runner)
        response = DeliveryRunDetailResponse(
            id=run.id,
            name=run.name,
            runner=runner_label,
            vehicle=run.vehicle,
            status=run.status,
            start_time=run.start_time,
            end_time=run.end_time,
            updated_at=run.updated_at,
            orders=[
                OrderSummary(
                    id=o.id,
                    inflow_order_id=o.inflow_order_id,
                    recipient_name=o.recipient_name,
                    delivery_location=o.delivery_location,
                    status=o.status,
                    delivery_sequence=o.delivery_sequence,
                )
                for o in sorted(
                    run.orders,
                    key=lambda order: (
                        order.delivery_sequence
                        if order.delivery_sequence is not None
                        else 999999,
                        order.updated_at or order.created_at,
                    ),
                )
            ],
        )
        return jsonify(response.model_dump(mode="json"))


@bp.route("/<run_id>/finish", methods=["PUT"])
@require_auth
def finish_run(run_id):
    """Finish a delivery run and complete already delivered local partial legs."""
    data = request.get_json() or {}

    try:
        req = FinishDeliveryRunRequest(**data)
    except PydanticValidationError as exc:
        raise ValidationError(
            "Invalid finish run request", details={"errors": exc.errors()}
        )

    with get_db() as db:
        service = DeliveryRunService(db)
        try:
            run = service.finish_run(
                UUID(run_id),
                create_remainders=req.create_remainders,
                expected_updated_at=req.expected_updated_at,
            )

            # Broadcast via SocketIO in background
            broadcast_dedup.request_broadcast(_broadcast_active_runs_sync)
            broadcast_dedup.request_broadcast(broadcast_vehicle_status_update_sync)

            response = _delivery_run_response(run, db)
            return jsonify(response)
        except ValueError as e:
            abort(400, description=str(e))


@bp.route("/<run_id>/orders/<order_id>/recall", methods=["PUT"])
@require_auth
def recall_run_order(run_id, order_id):
    """Recall an undeliverable order from an active run."""
    data = request.get_json() or {}

    try:
        req = RecallDeliveryRunOrderRequest(**data)
    except PydanticValidationError as exc:
        raise ValidationError(
            "Invalid recall request", details={"errors": exc.errors()}
        )

    with get_db() as db:
        service = DeliveryRunService(db)
        run = service.recall_order_from_run(
            UUID(run_id),
            UUID(order_id),
            reason=req.reason,
            expected_updated_at=req.expected_updated_at,
        )

        broadcast_dedup.request_broadcast(_broadcast_active_runs_sync)
        broadcast_dedup.request_broadcast(broadcast_vehicle_status_update_sync)

        response = _delivery_run_response(run, db)
        return jsonify(response)


@bp.route("/<run_id>/orders/reorder", methods=["PUT"])
@require_auth
def reorder_run_orders(run_id):
    """Persist display/delivery order for orders assigned to an active run."""
    data = request.get_json() or {}

    try:
        req = ReorderDeliveryRunOrdersRequest(**data)
    except PydanticValidationError as exc:
        raise ValidationError(
            "Invalid reorder request", details={"errors": exc.errors()}
        )

    with get_db() as db:
        service = DeliveryRunService(db)
        run = service.reorder_run_orders(
            UUID(run_id),
            order_ids=req.order_ids,
            expected_updated_at=req.expected_updated_at,
        )

        broadcast_dedup.request_broadcast(_broadcast_active_runs_sync)

        response = _delivery_run_response(run, db)
        return jsonify(response)


# SocketIO event handlers will be registered in main.py
