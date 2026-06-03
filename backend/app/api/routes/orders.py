from flask import Blueprint, request, jsonify, abort, send_file, current_app, g
from flask_socketio import emit
from sqlalchemy import or_, func
from sqlalchemy.orm import Session, object_session
from typing import Optional, List
from uuid import UUID
from pathlib import Path
from datetime import datetime

from app.database import get_db
from app.models.user import User
from app.services.order_service import OrderService
from app.services.order_splitting import OrderSplittingService
from app.services.inflow_service import InflowService
from app.utils.broadcast_dedup import broadcast_dedup

from app.schemas.order import (
    OrderResponse,
    OrderDetailResponse,
    OrderDismissRequest,
    OrderStatusUpdate,
    OrderRollbackUpdate,
    BulkStatusUpdate,
    OrderUpdate,
    AssetTagUpdate,
    PicklistGenerationRequest,
    QASubmission,
    SignatureData,
    ShippingWorkflowUpdateRequest,
    ShippingWorkflowResponse,
    PickStatus,
)
from app.models.order import OrderStatus
from app.models.order import Order
from app.schemas.audit import AuditLogResponse
from app.utils.exceptions import (
    ConflictError,
    DNSApiError,
    NotFoundError,
    ValidationError,
)
from app.utils.display_labels import resolve_runner_display, resolve_user_display
from app.utils.timezone import to_utc_iso_z
from app.api.auth_middleware import (
    get_current_user_display_name,
    get_current_user_email,
    require_auth,
    require_admin,
)
import logging

bp = Blueprint("orders", __name__)
bp.strict_slashes = False
logger = logging.getLogger(__name__)

# Simple in-memory broadcaster for SocketIO clients
_order_clients = set()


def _resolve_order_user_fields(data: dict, db_session) -> dict:
    """Resolve raw email/user identifiers to display names in order response data."""
    if not db_session:
        return data
    user_fields = [
        "assigned_deliverer",
        "tagged_by",
        "picklist_generated_by",
        "qa_completed_by",
        "shipping_workflow_status_updated_by",
        "shipped_to_carrier_by",
    ]
    for field in user_fields:
        raw = data.get(field)
        if raw and isinstance(raw, str) and "@" in raw:
            data[field] = resolve_user_display(db_session, raw)
    return data


def _resolve_asset_tag_required(
    data: dict,
    order,
    inflow_service: Optional[InflowService] = None,
    asset_tag_requirement_cache: Optional[dict[tuple[object, ...], bool]] = None,
) -> dict:
    inflow_data = getattr(order, "inflow_data", None)
    if not inflow_data:
        data["asset_tag_required"] = False
        return data

    tag_requirement_source = inflow_data
    if getattr(order, "remainder_order_id", None) and not getattr(
        order, "parent_order_id", None
    ):
        db_session = object_session(order)
        if db_session is not None:
            remainder_view = OrderSplittingService(db_session).build_parent_remainder_document_view(
                order
            )
            if remainder_view is not None:
                tag_requirement_source = remainder_view

    try:
        if inflow_service is not None and asset_tag_requirement_cache is not None:
            data["asset_tag_required"] = inflow_service.requires_asset_tags_cached(
                tag_requirement_source,
                asset_tag_requirement_cache,
            )
        elif inflow_service is not None:
            data["asset_tag_required"] = inflow_service.requires_asset_tags(
                tag_requirement_source
            )
        else:
            data["asset_tag_required"] = False
    except Exception as exc:
        logger.warning(
            "Failed to compute asset_tag_required for order %s: %s",
            getattr(order, "inflow_order_id", None) or getattr(order, "id", None),
            exc,
        )
        data["asset_tag_required"] = False

    return data


def _order_response_json(order, db_session=None) -> dict:
    data = current_app.json.loads(OrderResponse.model_validate(order).model_dump_json())
    data = _resolve_asset_tag_required(
        data,
        order,
        InflowService() if getattr(order, "inflow_data", None) else None,
    )
    return _resolve_order_user_fields(data, db_session)


def _order_detail_response_json(order, db_session=None) -> dict:
    data = current_app.json.loads(
        OrderDetailResponse.model_validate(order).model_dump_json()
    )
    inflow_service = InflowService() if getattr(order, "inflow_data", None) else None
    if db_session:
        linked_ids = {
            "parent_order_id": data.get("parent_order_id"),
            "remainder_order_id": data.get("remainder_order_id"),
        }
        for relation_field, linked_order_id in linked_ids.items():
            inflow_field = relation_field.replace("_order_id", "_inflow_order_id")
            if not linked_order_id:
                data[inflow_field] = None
                continue
            linked_order = db_session.query(Order).filter(Order.id == linked_order_id).first()
            data[inflow_field] = linked_order.inflow_order_id if linked_order else None
    data = _resolve_asset_tag_required(data, order, inflow_service)
    return _resolve_order_user_fields(data, db_session)


def _get_current_user_display_name() -> str:
    return get_current_user_display_name()


def _order_list_item_json(order, pick_status_data=None) -> str:
    response_model = OrderResponse.model_validate(order)
    if pick_status_data is not None:
        try:
            response_model = response_model.model_copy(
                update={"pick_status": PickStatus.model_validate(pick_status_data)}
            )
        except Exception as exc:
            logger.warning(
                "Skipping invalid pick_status for order %s: %s",
                getattr(order, "inflow_order_id", None) or getattr(order, "id", None),
                exc,
            )
    return response_model.model_dump_json()


def _serialize_utc_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return to_utc_iso_z(value)


def _serialize_order_list_item(
    order,
    pick_status_data=None,
    db_session=None,
    inflow_service: Optional[InflowService] = None,
    asset_tag_requirement_cache: Optional[dict[tuple[object, ...], bool]] = None,
) -> dict:
    latest_job = getattr(order, "latest_picklist_print_job", None)
    latest_job_payload = None
    if latest_job is not None:
        latest_job_payload = {
            "id": str(latest_job.id),
            "status": latest_job.status,
            "trigger_source": latest_job.trigger_source,
            "requested_by": latest_job.requested_by,
            "attempt_count": latest_job.attempt_count,
            "created_at": _serialize_utc_datetime(latest_job.created_at),
            "completed_at": _serialize_utc_datetime(latest_job.completed_at),
            "last_error": latest_job.last_error,
        }

    data = {
        "id": str(order.id),
        "inflow_order_id": order.inflow_order_id,
        "inflow_sales_order_id": order.inflow_sales_order_id,
        "recipient_name": order.recipient_name,
        "recipient_contact": order.recipient_contact,
        "delivery_location": order.delivery_location,
        "po_number": order.po_number,
        "status": order.status,
        "assigned_deliverer": order.assigned_deliverer,
        "issue_reason": order.issue_reason,
        "tagged_at": _serialize_utc_datetime(order.tagged_at),
        "tagged_by": order.tagged_by,
        "tag_data": order.tag_data,
        "picklist_generated_at": _serialize_utc_datetime(order.picklist_generated_at),
        "picklist_generated_by": order.picklist_generated_by,
        "picklist_path": order.picklist_path,
        "delivery_run_id": order.delivery_run_id,
        "delivery_sequence": order.delivery_sequence,
        "qa_completed_at": _serialize_utc_datetime(order.qa_completed_at),
        "qa_completed_by": order.qa_completed_by,
        "qa_data": order.qa_data,
        "qa_path": order.qa_path,
        "qa_method": order.qa_method,
        "signature_captured_at": _serialize_utc_datetime(order.signature_captured_at),
        "signed_picklist_path": order.signed_picklist_path,
        "bundle_path": order.bundle_path,
        "order_details_path": order.order_details_path,
        "order_details_generated_at": _serialize_utc_datetime(
            order.order_details_generated_at
        ),
        "order_details_email_status": order.order_details_email_status,
        "order_details_email_status_updated_at": _serialize_utc_datetime(
            order.order_details_email_status_updated_at
        ),
        "shipping_workflow_status": order.shipping_workflow_status,
        "shipping_workflow_status_updated_at": _serialize_utc_datetime(
            order.shipping_workflow_status_updated_at
        ),
        "shipping_workflow_status_updated_by": order.shipping_workflow_status_updated_by,
        "shipped_to_carrier_at": _serialize_utc_datetime(order.shipped_to_carrier_at),
        "shipped_to_carrier_by": order.shipped_to_carrier_by,
        "carrier_name": order.carrier_name,
        "tracking_number": order.tracking_number,
        "parent_order_id": order.parent_order_id,
        "has_remainder": order.has_remainder,
        "remainder_order_id": order.remainder_order_id,
        "created_at": _serialize_utc_datetime(order.created_at),
        "updated_at": _serialize_utc_datetime(order.updated_at),
        "pick_status": pick_status_data,
        "latest_picklist_print_job": latest_job_payload,
    }
    data = _resolve_asset_tag_required(
        data,
        order,
        inflow_service,
        asset_tag_requirement_cache,
    )
    return _resolve_order_user_fields(data, db_session)


def _broadcast_orders_sync(db_session: Session = None):
    """Send current orders to all connected clients (sync version)."""
    if db_session is not None:
        _do_broadcast_orders(db_session)
        return

    from app.database import get_db

    with get_db() as db:
        _do_broadcast_orders(db)


def _do_broadcast_orders(db_session):
    try:
        service = OrderService(db_session)
        orders, _ = service.get_orders(limit=1000)
        payload = []
        for order in orders:
            raw_deliverer = order.assigned_deliverer
            deliverer_label = resolve_user_display(db_session, raw_deliverer, raw_deliverer) if raw_deliverer and "@" in raw_deliverer else raw_deliverer
            payload.append(
                {
                    "id": str(order.id),
                    "inflow_order_id": order.inflow_order_id,
                    "recipient_name": order.recipient_name,
                    "status": order.status,
                    "updated_at": _serialize_utc_datetime(order.updated_at),
                    "delivery_location": order.delivery_location,
                    "assigned_deliverer": deliverer_label,
                }
            )

        # Emit via SocketIO to all connected clients in 'orders' room
        try:
            from app.main import socketio

            socketio.emit(
                "orders_update",
                {"type": "orders_update", "data": payload},
                room="orders",
            )
        except Exception as e:
            import logging

            logging.getLogger(__name__).error(f"Failed to broadcast orders: {e}")
    except Exception:
        logger.exception("Failed to broadcast orders")


@bp.route("/", methods=["GET"])
@require_auth
def get_orders():
    """Get orders with filters and pagination"""
    status = request.args.get("status")
    search = request.args.get("search")
    skip = request.args.get("skip", 0, type=int)
    limit = request.args.get("limit", 100, type=int)

    # Validate limit
    limit = max(1, min(limit, 200))
    skip = max(0, skip)

    # Convert status string to enum if provided
    status_enum = None
    if status:
        try:
            status_enum = OrderStatus(status)
        except ValueError:
            pass

    with get_db() as db:
        service = OrderService(db)
        inflow_service = InflowService()
        asset_tag_requirement_cache: dict[tuple[object, ...], bool] = {}
        orders, total = service.get_orders(
            status=status_enum, search=search, skip=skip, limit=limit
        )

        # Enrich orders with pick_status for Pre-Delivery queue visibility
        include_pick_status = status_enum in {
            OrderStatus.PRE_DELIVERY,
            OrderStatus.IN_DELIVERY,
        }
        result = []
        for o in orders:
            pick_status_data = None

            # Compute pick_status from inflow_data if available
            if include_pick_status and o.inflow_data:
                try:
                    pick_status_source = o.inflow_data
                    if o.remainder_order_id and not o.parent_order_id:
                        remainder_pick_source = OrderSplittingService(db).build_parent_remainder_pick_status_source(o)
                        if remainder_pick_source is not None:
                            pick_status_source = remainder_pick_source
                    pick_status_data = inflow_service.get_pick_status(pick_status_source)
                except Exception as exc:
                    logger.warning(
                        "Failed to compute pick_status for order %s: %s",
                        o.inflow_order_id,
                        exc,
                    )

            result.append(
                _serialize_order_list_item(
                    o,
                    pick_status_data,
                    db,
                    inflow_service,
                    asset_tag_requirement_cache,
                )
            )

        return jsonify({
            "items": result,
            "total": total,
            "skip": skip,
            "limit": limit,
        })


@bp.route("/resolve", methods=["GET"])
@require_auth
def resolve_order_by_number():
    """Resolve an inFlow order number to internal UUID.

    Query param:
        order_number: inFlow order id (e.g. "TH3270")

    Returns:
        {"id": "<uuid>", "order_number": "<inflow_order_id>"}
    """
    order_number = (request.args.get("order_number") or "").strip()
    if not order_number:
        return jsonify({"error": "order_number is required"}), 400

    normalized = order_number.lower()

    with get_db() as db:
        order = (
            db.query(Order)
            .filter(Order.hidden_from_ops.is_(False))
            .filter(func.lower(Order.inflow_order_id) == normalized)
            .first()
        )
        if not order:
            abort(404, description="Order not found")

        return jsonify(
            {"id": str(order.id), "order_number": str(order.inflow_order_id)}
        )


@bp.route("/tag-request/candidates", methods=["GET"])
@require_auth
def get_tag_request_candidates():
    """Get picked orders that still need a tag request batch."""
    limit = request.args.get("limit", default=200, type=int)
    limit = max(1, min(limit, 1000))

    search = (request.args.get("search") or "").strip()

    with get_db() as db:
        inflow_service = InflowService()
        order_service = OrderService(db)
        asset_tag_requirement_cache: dict[tuple[object, ...], bool] = {}
        query = (
            db.query(Order)
            .filter(Order.hidden_from_ops.is_(False))
            .filter(Order.status == OrderStatus.PICKED.value)
            .filter(Order.tagged_at.is_(None))
        )

        if search:
            like = f"%{search}%"
            query = query.filter(
                or_(
                    Order.inflow_order_id.ilike(like),
                    Order.recipient_name.ilike(like),
                )
            )

        # JSON tag_data filtering is handled in Python for cross-DB compatibility.
        candidates: list[Order] = (
            query.order_by(Order.updated_at.desc()).limit(1000).all()
        )

        needing_request: list[dict] = []
        for order in candidates:
            if not order.inflow_data:
                continue
            if not inflow_service.requires_asset_tags_cached(
                order.inflow_data,
                asset_tag_requirement_cache,
            ):
                continue
            if order_service._parent_remainder_has_unpicked_items(order):
                continue

            needing_request.append(_order_response_json(order, db))
            if len(needing_request) >= limit:
                break

        return jsonify(needing_request)


@bp.route("/<order_id>", methods=["GET"])
@require_auth
def get_order(order_id):
    """Get order detail with audit logs and notifications"""
    with get_db() as db:
        service = OrderService(db)
        order = service.get_order_detail(order_id)
        if not order:
            abort(404, description="Order not found")
        if order.hidden_from_ops:
            abort(404, description="Order not found")
        response_data = _order_detail_response_json(order, db)
        if order.inflow_data:
            inflow_service = InflowService()
            order_view = order.inflow_data
            pick_status_source = order.inflow_data
            if order.remainder_order_id and not order.parent_order_id:
                splitting_service = OrderSplittingService(db)
                remainder_view = splitting_service.build_parent_remainder_document_view(order)
                remainder_pick_source = splitting_service.build_parent_remainder_pick_status_source(order)
                if remainder_view is not None:
                    order_view = remainder_view
                if remainder_pick_source is not None:
                    pick_status_source = remainder_pick_source
            response_data["inflow_data"] = order_view
            response_data["asset_tag_required"] = inflow_service.requires_asset_tags(
                order_view
            )
            response_data["asset_tag_serials"] = inflow_service.get_asset_tag_serials(
                order_view
            )
            try:
                response_data["pick_status"] = inflow_service.get_pick_status(pick_status_source)
            except Exception as exc:
                logger.warning(
                    "Failed to compute pick_status for order detail %s: %s",
                    order.inflow_order_id,
                    exc,
                )

        return jsonify(response_data)


@bp.route("/<order_id>/picklist", methods=["POST"])
@require_auth
def generate_picklist(order_id):
    """Generate a picklist PDF for an order."""
    data = request.get_json(silent=True) or {}
    request_payload = PicklistGenerationRequest(**data)

    # Use auth context for identity — the frontend may send a display name
    # in generated_by, but tagged_by stores email, so we compare against email.
    current_user_email = get_current_user_email()
    current_user_display = _get_current_user_display_name()
    # generated_by is the primary identifier (used for same-user comparison
    # against tagged_by which stores email). Override the frontend's value.
    generated_by = current_user_email

    with get_db() as db:
        service = OrderService(db)
        order = service.generate_picklist(
            order_id=order_id,
            generated_by=generated_by,
            generated_by_display=current_user_display,
            expected_updated_at=request_payload.expected_updated_at,
            create_partial_leg=bool(request_payload.create_partial_leg and request_payload.confirm_create_partial_leg),
        )
        return jsonify(_order_response_json(order, db))


@bp.route("/<order_id>", methods=["PATCH"])
@require_auth
def update_order(order_id):
    """Update order fields"""
    data = request.get_json()

    with get_db() as db:
        service = OrderService(db)
        order_id_str = str(order_id)
        order = service._resolve_order(order_id_str, lock=True)
        if not order:
            abort(404, description="Order not found")

        update = OrderUpdate(**data)
        service.assert_not_stale(order, update.expected_updated_at)

        for field, value in update.model_dump(
            exclude_unset=True, exclude={"expected_updated_at"}
        ).items():
            setattr(order, field, value)

        order.updated_at = datetime.utcnow()
        logger.info(
            "Order fields updated: order_id=%s fields=%s",
            order.id,
            sorted(
                update.model_dump(
                    exclude_unset=True, exclude={"expected_updated_at"}
                ).keys()
            ),
        )

        db.commit()
        db.refresh(order)
        return jsonify(_order_response_json(order, db))


@bp.route("/<order_id>/status", methods=["PATCH"])
@require_auth
def update_order_status(order_id):
    """Transition order status"""
    data = request.get_json()
    changed_by = request.args.get("changed_by") or get_current_user_display_name()

    with get_db() as db:
        service = OrderService(db)
        status_update = OrderStatusUpdate(**data)
        order = service.transition_status(
            order_id=order_id,
            new_status=status_update.status,
            changed_by=changed_by,
            reason=status_update.reason,
            expected_updated_at=status_update.expected_updated_at,
        )

        # Send notifications based on new status
        if status_update.status == OrderStatus.IN_DELIVERY:
            from app.services.teams_recipient_service import teams_recipient_service

            teams_recipient_service.notify_orders_in_delivery([order])

        # Broadcast order update via SocketIO
        broadcast_dedup.request_broadcast(_broadcast_orders_sync)

        return jsonify(_order_response_json(order, db))


@bp.route("/<order_id>/rollback", methods=["PATCH"])
@require_auth
def rollback_order_status(order_id):
    """Rollback order status to an earlier workflow state."""
    data = request.get_json()
    changed_by = request.args.get("changed_by") or get_current_user_display_name()

    with get_db() as db:
        service = OrderService(db)
        rollback_update = OrderRollbackUpdate(**data)
        order = service.rollback_status(
            order_id=order_id,
            target_status=rollback_update.status,
            changed_by=changed_by,
            reason=rollback_update.reason,
            expected_updated_at=rollback_update.expected_updated_at,
        )

        broadcast_dedup.request_broadcast(_broadcast_orders_sync)

        return jsonify(_order_response_json(order, db))


@bp.route("/<order_id>/dismiss", methods=["POST"])
@require_admin
def dismiss_order(order_id):
    """Hide an order from operational views and reporting."""
    data = request.get_json() or {}
    changed_by = get_current_user_display_name()

    with get_db() as db:
        service = OrderService(db)
        dismiss_request = OrderDismissRequest(**data)
        order = service.dismiss_order(
            order_id=order_id,
            changed_by=changed_by,
            reason=dismiss_request.reason,
            remove_sharepoint_files=dismiss_request.remove_sharepoint_files,
            expected_updated_at=dismiss_request.expected_updated_at,
        )

        broadcast_dedup.request_broadcast(_broadcast_orders_sync)

        return jsonify(_order_response_json(order, db))


@bp.route("/bulk-transition", methods=["POST"])
@require_admin
def bulk_transition_status():
    """Bulk transition multiple orders"""
    data = request.get_json()
    changed_by = request.args.get("changed_by") or get_current_user_display_name()

    with get_db() as db:
        service = OrderService(db)
        bulk_update = BulkStatusUpdate(**data)
        orders = service.bulk_transition(
            order_ids=bulk_update.order_ids,
            new_status=bulk_update.status,
            changed_by=changed_by,
            reason=bulk_update.reason,
        )

        # Trigger Teams notifications for bulk transitions to In Delivery
        if bulk_update.status == OrderStatus.IN_DELIVERY:
            from app.services.teams_recipient_service import teams_recipient_service

            teams_recipient_service.notify_orders_in_delivery(orders)

        # Broadcast order updates via SocketIO
        broadcast_dedup.request_broadcast(_broadcast_orders_sync)

        return jsonify([_order_response_json(o, db) for o in orders])


@bp.route("/<order_id>/audit", methods=["GET"])
@require_auth
def get_order_audit(order_id):
    """Get audit log for an order"""
    with get_db() as db:
        service = OrderService(db)
        order = service.get_order_detail(order_id)
        if not order:
            abort(404, description="Order not found")

        return jsonify(
            [
                AuditLogResponse.model_validate(log).model_dump(mode="json")
                for log in order.audit_logs
            ]
        )


@bp.route("/<order_id>/tag", methods=["POST"])
@require_auth
def tag_order(order_id):
    """Mock asset tagging step"""
    data = request.get_json()

    with get_db() as db:
        service = OrderService(db)
        tag_update = AssetTagUpdate(**data)

        # Auto-assign technician from auth context, fallback to payload for tests/systems
        current_user = get_current_user_email()
        technician = current_user if current_user != "system" else tag_update.technician

        order = service.mark_asset_tagged(
            order_id=order_id,
            tag_ids=tag_update.tag_ids,
            technician=technician,
            expected_updated_at=tag_update.expected_updated_at,
        )

        broadcast_dedup.request_broadcast(_broadcast_orders_sync)
        return jsonify(_order_response_json(order, db))


@bp.route("/<order_id>/qa", methods=["POST"])
@require_auth
def submit_qa(order_id):
    """Submit QA checklist for an order"""
    data = request.get_json()

    with get_db() as db:
        service = OrderService(db)
        submission = QASubmission(**data)

        # Auto-assign technician from auth context
        current_user = get_current_user_email()
        technician = _get_current_user_display_name() if current_user != "system" else submission.technician

        order = service.submit_qa(
            order_id=order_id,
            qa_data=submission.responses,
            technician=technician,
            technician_identifier=current_user,
            expected_updated_at=submission.expected_updated_at,
        )

        # Broadcast order update via SocketIO
        broadcast_dedup.request_broadcast(_broadcast_orders_sync)

        return jsonify(_order_response_json(order, db))


@bp.route("/<order_id>/picklist", methods=["GET"])
@require_auth
def get_picklist(order_id):
    """Download generated picklist PDF (from local storage or SharePoint)"""
    from io import BytesIO

    with get_db() as db:
        service = OrderService(db)
        order = service.get_order_detail(order_id)
        if not order:
            abort(404, description="Order not found")
        if not order.picklist_path:
            abort(404, description="Picklist not generated")

        picklist_path = order.picklist_path

        # Check if this is a SharePoint URL
        if picklist_path.startswith("http"):
            # Download from SharePoint
            try:
                from app.services.sharepoint_service import get_sharepoint_service

                sp_service = get_sharepoint_service()

                # Extract filename from the path stored in order
                # The path format is the SharePoint web URL
                filename = f"{order.inflow_order_id}.pdf"

                # Download the file from SharePoint
                pdf_bytes = sp_service.download_file("picklists", filename)
                if not pdf_bytes:
                    abort(404, description="Picklist file not found in SharePoint")

                pdf_stream = BytesIO(pdf_bytes)
                pdf_stream.seek(0)

                return send_file(
                    pdf_stream,
                    mimetype="application/pdf",
                    as_attachment=False,
                    download_name=filename,
                )
            except Exception as e:
                import logging

                logging.error(f"Failed to download picklist from SharePoint: {e}")
                abort(500, description="Failed to download picklist")
        else:
            # Local file path
            path = Path(picklist_path)
            if not path.exists():
                abort(404, description="Picklist file missing")

            return send_file(
                path.resolve(), mimetype="application/pdf", download_name=path.name
            )

@bp.route("/<order_id>/signed-picklist", methods=["GET"])
@require_auth
def get_signed_picklist(order_id):
    """Download signed picklist PDF (from SharePoint or local storage)"""
    from io import BytesIO

    with get_db() as db:
        service = OrderService(db)
        order = service.get_order_detail(order_id)
        if not order:
            abort(404, description="Order not found")
        if not order.signed_picklist_path:
            abort(404, description="Signed picklist not found — order may not be signed yet")

        signed_path = order.signed_picklist_path

        # Check if this is a SharePoint URL
        if signed_path.startswith("http"):
            try:
                from app.services.sharepoint_service import get_sharepoint_service

                sp_service = get_sharepoint_service()

                filename = f"{order.inflow_order_id}_signed.pdf"

                pdf_bytes = sp_service.download_file("signed", filename)
                if not pdf_bytes:
                    abort(404, description="Signed picklist file not found in SharePoint")

                pdf_stream = BytesIO(pdf_bytes)
                pdf_stream.seek(0)

                return send_file(
                    pdf_stream,
                    mimetype="application/pdf",
                    as_attachment=False,
                    download_name=filename,
                )
            except Exception as e:
                import logging

                logging.error(f"Failed to download signed picklist from SharePoint: {e}")
                abort(500, description="Failed to download signed picklist")
        else:
            # Local file path
            path = Path(signed_path)
            if not path.exists():
                abort(404, description="Signed picklist file missing")

            return send_file(
                path.resolve(), mimetype="application/pdf", download_name=path.name
            )


@bp.route("/<order_id>/fulfill", methods=["POST"])
@require_admin
def fulfill_order(order_id):
    """Mark an order as fulfilled in Inflow (best-effort)."""
    with get_db() as db:
        service = OrderService(db)
        order = service.get_order_detail(order_id)
        if not order:
            abort(404, description="Order not found")
        if not order.inflow_sales_order_id:
            abort(400, description="Order missing inflow_sales_order_id")

        inflow_service = InflowService()
        result = inflow_service.fulfill_sales_order_sync(
            order.inflow_sales_order_id, db=db, user_id="system"
        )
        return jsonify({"success": True, "result": result})


@bp.route("/<order_id>/sign", methods=["POST"])
@require_auth
def sign_order(order_id):
    """Complete order signing, generate bundled documents, and transition to Delivered status"""
    data = request.get_json()
    signature_data = SignatureData(**data)
    import logging
    logging.getLogger(__name__).info(
        "SIG_ROUTE order_id=%s page_width=%s page_height=%s placements=%s",
        order_id, signature_data.page_width, signature_data.page_height,
        [{"p": p.page_number, "x": p.x, "y": p.y, "w": p.width, "h": p.height} for p in signature_data.placements]
    )

    # Phase 1: generate documents BEFORE locking the order row (no DB lock held during PDF I/O)
    with get_db() as db:
        service = OrderService(db)
        signed_sp_url, bundle_sp_url = service.generate_bundled_documents(
            order_id=order_id,
            signature_data=signature_data.model_dump(exclude={"expected_updated_at"}),
        )
        order = service.get_order_detail(order_id)
        if not order:
            raise NotFoundError("Order", str(order_id))

        updated_inflow_order = None
        proof_of_delivery_updated = False
        if order.inflow_sales_order_id:
            try:
                inflow_service = InflowService()
                updated_inflow_order = inflow_service.update_proof_of_delivery_url_sync(
                    order.inflow_sales_order_id,
                    bundle_sp_url,
                )
                proof_of_delivery_updated = True
            except Exception as exc:
                logger.error(
                    "Failed to update inFlow customFields.custom5 for %s after signing: %s",
                    order.inflow_order_id or order.id,
                    exc,
                )
        else:
            logger.warning(
                "Order %s has no inflow_sales_order_id; skipping Proof of Delivery link update",
                order.inflow_order_id or order.id,
            )
        # generate_bundled_documents now stores signed_picklist_path on the order internally
        signed_picklist_path = signed_sp_url
        bundled_path = bundle_sp_url

    # Phase 2: short locking transaction for status update + commit
    with get_db() as db:
        service = OrderService(db)

        order = (
            db.query(Order).filter(Order.id == str(order_id)).with_for_update().first()
        )
        if not order:
            raise NotFoundError("Order", str(order_id))

        if order.status != OrderStatus.IN_DELIVERY.value:
            raise ValidationError(
                f"Order must be in In Delivery status to sign. Current status: {order.status}",
                details={
                    "current_status": order.status,
                    "required_status": OrderStatus.IN_DELIVERY.value,
                },
            )

        service.assert_not_stale(order, signature_data.expected_updated_at)

        from datetime import datetime

        order = service.transition_status(
            order_id=order_id, new_status=OrderStatus.DELIVERED, changed_by="system"
        )

        order.signature_captured_at = datetime.utcnow()
        order.signed_picklist_path = signed_picklist_path
        order.bundle_path = bundled_path
        if updated_inflow_order is not None:
            order.inflow_data = updated_inflow_order
        order.updated_at = datetime.utcnow()

        delivery_vehicle = order.delivery_run.vehicle if order.delivery_run else None
        if delivery_vehicle:
            from app.services.vehicle_checkout_service import VehicleCheckoutService

            try:
                checkout_service = VehicleCheckoutService(db)
                checkout_service.checkin(
                    vehicle=delivery_vehicle,
                    notes=f"Auto check-in after completing delivery for {order.inflow_order_id or order.id}",
                    allow_active_delivery_run=True,
                )
            except Exception as exc:
                import logging

                logging.warning(
                    "Auto check-in failed after signing order %s on vehicle %s: %s",
                    order.inflow_order_id or order.id,
                    delivery_vehicle,
                    exc,
                )

        db.commit()
        db.refresh(order)

        # Broadcast order update via SocketIO
        broadcast_dedup.request_broadcast(_broadcast_orders_sync)

        return jsonify(
            {
                "success": True,
                "message": "Order signed and bundled documents generated",
                "bundled_document_path": bundled_path,
                "signed_picklist_path": signed_picklist_path,
                "proof_of_delivery_updated": proof_of_delivery_updated,
            }
        )


@bp.route("/<order_id>/shipping-workflow", methods=["PATCH"])
@require_auth
def update_shipping_workflow(order_id):
    """Update shipping workflow status for an order"""
    data = request.get_json()

    with get_db() as db:
        service = OrderService(db)
        req = ShippingWorkflowUpdateRequest(**data)
        order = service.transition_shipping_workflow(
            order_id=order_id,
            new_status=req.status,
            carrier_name=req.carrier_name,
            tracking_number=req.tracking_number,
            updated_by=req.updated_by,
            expected_updated_at=req.expected_updated_at,
        )

        # Broadcast order update via SocketIO
        broadcast_dedup.request_broadcast(_broadcast_orders_sync)

        return jsonify(_order_response_json(order, db))


@bp.route("/<order_id>/shipping-workflow", methods=["GET"])
@require_auth
def get_shipping_workflow(order_id):
    """Get shipping workflow status for an order"""
    with get_db() as db:
        service = OrderService(db)
        order = service.get_order_by_id(order_id)
        if not order:
            abort(404, description="Order not found")

        response = ShippingWorkflowResponse(
            shipping_workflow_status=order.shipping_workflow_status,
            shipping_workflow_status_updated_at=order.shipping_workflow_status_updated_at,
            shipping_workflow_status_updated_by=order.shipping_workflow_status_updated_by,
            shipped_to_carrier_at=order.shipped_to_carrier_at,
            shipped_to_carrier_by=order.shipped_to_carrier_by,
            carrier_name=order.carrier_name,
            tracking_number=order.tracking_number,
        )
        return jsonify(response.model_dump())


@bp.route("/<order_id>/order-details.pdf", methods=["GET"])
@require_auth
def get_order_details_pdf(order_id):
    """Generate and download Order Details PDF"""
    from app.services.pdf_service import pdf_service
    from io import BytesIO

    with get_db() as db:
        service = OrderService(db)
        order = service.get_order_by_id(order_id)
        if not order:
            abort(404, description="Order not found")
        if not order.inflow_data:
            abort(404, description="Order has no inFlow data")

        inflow_service = InflowService()
        order_details_data = inflow_service.build_remaining_order_view(order.inflow_data)
        remainder_mode = bool(
            getattr(order, "remainder_order_id", None)
            and not getattr(order, "parent_order_id", None)
        )
        if remainder_mode:
            remainder_view = OrderSplittingService(db).build_parent_remainder_document_view(order)
            if remainder_view is not None:
                order_details_data = remainder_view
        elif not (
            order_details_data.get("lines")
            and order_details_data.get("lines") != order.inflow_data.get("lines", [])
        ):
            order_details_data = order.inflow_data

        # Generate PDF
        try:
            pdf_bytes = pdf_service.generate_order_details_pdf(order_details_data)
            pdf_stream = BytesIO(pdf_bytes)
            pdf_stream.seek(0)

            filename = f"OrderDetails_{order.inflow_order_id}.pdf"
            return send_file(
                pdf_stream,
                mimetype="application/pdf",
                as_attachment=False,
                download_name=filename,
            )
        except Exception as e:
            import logging

            logging.error(f"Failed to generate Order Details PDF: {e}")
            abort(500, description="Failed to generate PDF")


@bp.route("/<order_id>/send-order-details", methods=["POST"])
@require_auth
def send_order_details_email(order_id):
    """Generate Order Details PDF and email to recipient"""
    with get_db() as db:
        service = OrderService(db)
        current_user = get_current_user_email()
        generated_by = current_user if current_user != "system" else None

        success = service.send_order_details_email(
            order_id=order_id,
            generated_by=generated_by,
        )

        if success:
            broadcast_dedup.request_broadcast(_broadcast_orders_sync)
            return jsonify(
                {
                    "success": True,
                    "message": "Order Details PDF sent successfully",
                }
            )

        abort(
            500,
            description="Failed to send Order Details email or update order remarks.",
        )


# SocketIO event handlers will be registered in main.py
