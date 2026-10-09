"""Admin-only, read-only product comparison applet."""

from flask import Blueprint, g, jsonify

from app.api.auth_middleware import require_admin
from app.config import settings
from app.services.product_checker_service import ProductCheckerService


bp = Blueprint("product_checker", __name__, url_prefix="/api/system/product-checker")
product_checker_service = ProductCheckerService(settings)


@bp.route("", methods=["GET"])
@require_admin
def get_product_checker():
    return jsonify(product_checker_service.status(include_report=True))


@bp.route("/status", methods=["GET"])
@require_admin
def get_product_checker_status():
    return jsonify(product_checker_service.status())


@bp.route("/refresh", methods=["POST"])
@require_admin
def refresh_product_checker():
    payload, created = product_checker_service.start(str(getattr(g, "user_email", None) or g.user_id))
    return jsonify({**payload, "created": created}), 202 if created else 200
