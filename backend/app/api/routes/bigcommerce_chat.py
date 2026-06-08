from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request

from app.api.auth_middleware import require_admin
from app.services.bigcommerce_chat_service import (
    BigCommerceChatError,
    ask_bigcommerce_chat,
)
from app.services.bigcommerce_analytics_cache import (
    get_bigcommerce_cache_status,
    sync_bigcommerce_catalog_cache,
    sync_bigcommerce_analytics_cache,
)


bp = Blueprint("bigcommerce_chat", __name__)
logger = logging.getLogger(__name__)


@bp.route("", methods=["POST"])
@require_admin
def chat() -> Any:
    payload = request.get_json(silent=True) or {}
    question = payload.get("question")
    messages = payload.get("messages")

    if not isinstance(question, str) or not question.strip():
        return jsonify({"error": "Question is required."}), 400

    if messages is not None and not isinstance(messages, list):
        return jsonify({"error": "Messages must be a list when provided."}), 400

    try:
        return jsonify(ask_bigcommerce_chat(question, messages))
    except BigCommerceChatError as exc:
        logger.warning("BigCommerce chat request failed: %s", exc)
        return jsonify({"error": str(exc)}), 502


@bp.route("/cache/status", methods=["GET"])
@require_admin
def cache_status() -> Any:
    return jsonify(get_bigcommerce_cache_status())


@bp.route("/cache/sync", methods=["POST"])
@require_admin
def cache_sync() -> Any:
    payload = request.get_json(silent=True) or {}
    full_backfill = bool(payload.get("full_backfill", False))
    max_orders = payload.get("max_orders", 5000)
    try:
        max_orders = int(max_orders)
    except (TypeError, ValueError):
        return jsonify({"error": "max_orders must be an integer."}), 400
    try:
        return jsonify(
            sync_bigcommerce_analytics_cache(
                full_backfill=full_backfill,
                max_orders=max_orders,
            )
        )
    except Exception as exc:
        logger.warning("BigCommerce cache sync failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 502


@bp.route("/cache/catalog-sync", methods=["POST"])
@require_admin
def catalog_cache_sync() -> Any:
    payload = request.get_json(silent=True) or {}
    max_products = payload.get("max_products", 5000)
    try:
        max_products = int(max_products)
    except (TypeError, ValueError):
        return jsonify({"error": "max_products must be an integer."}), 400
    try:
        return jsonify(sync_bigcommerce_catalog_cache(max_products=max_products))
    except Exception as exc:
        logger.warning("BigCommerce catalog cache sync failed: %s", exc, exc_info=True)
        return jsonify({"error": str(exc)}), 502
