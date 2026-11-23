"""Authenticated packing, product weights, and FedEx sandbox shipping."""

from io import BytesIO

from flask import Blueprint, g, jsonify, request, send_file
from pydantic import ValidationError as SchemaError

from app.api.auth_middleware import is_current_user_admin, require_admin, require_auth
from app.database import get_db
from app.schemas.shipment import PackingDraft, ProductWeightUpdate, ShipmentCreate, ShipmentCreationRequest
from app.services.shipment_service import ShipmentService
from app.services.shipping_contacts import MAX_CSV_BYTES, import_fedex_contacts, saved_fedex_contacts
from app.utils.exceptions import ValidationError

bp = Blueprint("shipments", __name__, url_prefix="/api/shipping")


def parse(schema):
    try:
        return schema.model_validate(request.get_json(silent=True))
    except SchemaError as exc:
        errors = [{"field": ".".join(map(str, error["loc"])), "message": error["msg"]} for error in exc.errors()]
        first = errors[0]
        raise ValidationError(f"{first['field'] or 'Shipment'}: {first['message']}", details={"errors": errors}) from exc


def actor():
    return str(getattr(g, "user_email", None) or g.user_id)


@bp.get("/products")
@require_auth
def products():
    with get_db() as db:
        return jsonify({"products": ShipmentService(db).catalog()})


@bp.put("/products/<product_id>")
@require_admin
def save_product(product_id):
    update = parse(ProductWeightUpdate)
    with get_db() as db:
        ShipmentService(db).save_weight(product_id, update, actor())
        return jsonify({"success": True})


@bp.post("/products/import")
@require_admin
def import_products():
    with get_db() as db:
        return jsonify({"imported": ShipmentService(db).import_catalog(actor())})


@bp.get("/orders/<order_id>")
@require_auth
def workspace(order_id):
    with get_db() as db:
        selected_ids = [value for value in request.args.get("orders", "").split(",") if value]
        return jsonify(ShipmentService(db, is_admin=is_current_user_admin()).workspace(order_id, selected_ids or None, request.args.get("contact")))


@bp.get("/queue")
@require_auth
def shipping_queue():
    with get_db() as db:
        return jsonify(ShipmentService(db).shipping_queue())


@bp.get("/orders/<order_id>/contacts")
@require_auth
def contacts(order_id):
    with get_db() as db:
        ShipmentService(db).order(order_id)
        return jsonify({"source": "fedex_csv", "contacts": saved_fedex_contacts(db, request.args.get("search", ""))})


@bp.get("/contacts")
@require_auth
def address_book():
    with get_db() as db:
        return jsonify({"source": "fedex_csv", "contacts": saved_fedex_contacts(db, request.args.get("search", ""))})


@bp.post("/contacts/import")
@require_admin
def import_contacts():
    uploaded = request.files.get("file")
    if uploaded is None:
        raise ValidationError("Select a FedEx address-book CSV export")
    content = uploaded.stream.read(MAX_CSV_BYTES + 1)
    with get_db() as db:
        return jsonify({"imported": import_fedex_contacts(db, content, actor()), "source": "fedex_csv"})


@bp.put("/orders/<order_id>/packing")
@require_auth
def save_packing(order_id):
    draft = parse(PackingDraft)
    with get_db() as db:
        service = ShipmentService(db, is_admin=is_current_user_admin())
        return jsonify(service.serialize(service.save_draft(order_id, draft, actor()), db))


@bp.post("/orders/<order_id>/create")
@require_auth
def create_shipment(order_id):
    payload = parse(ShipmentCreationRequest)
    with get_db() as db:
        service = ShipmentService(db, is_admin=is_current_user_admin())
        return jsonify(service.serialize(service.create(order_id, payload.expected_revision, actor(), confirm_high_shipping_cost=payload.confirm_high_shipping_cost), db))


@bp.post("/orders/<order_id>/rates")
@require_auth
def quote_rates(order_id):
    payload = parse(ShipmentCreate)
    with get_db() as db:
        service = ShipmentService(db, is_admin=is_current_user_admin())
        return jsonify(service.serialize(service.quote_rates(order_id, payload.expected_revision, actor()), db))


@bp.get("/orders/<order_id>/labels/<int:index>")
@require_auth
def label(order_id, index):
    with get_db() as db:
        service = ShipmentService(db, is_admin=is_current_user_admin())
        service.order(order_id)
        shipment = service.get(order_id)
        if not shipment:
            raise ValidationError("Create the shipment first")
        return send_file(BytesIO(service.label(shipment.id, index)), mimetype="application/pdf",
                         download_name=f"sandbox-{order_id}-box-{index + 1}.pdf", as_attachment=True)


@bp.post("/orders/<order_id>/labels/<int:index>/print")
@require_auth
def reprint(order_id, index):
    with get_db() as db:
        service = ShipmentService(db, is_admin=is_current_user_admin())
        service.order(order_id)
        shipment = service.get(order_id)
        if not shipment:
            raise ValidationError("Create the shipment first")
        job = service.queue_label(shipment, index, actor())
        db.commit()
        return jsonify({"job_id": job.id, "status": job.status})
