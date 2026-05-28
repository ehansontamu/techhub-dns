from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, jsonify, request

from app.api.auth_middleware import require_admin
from app.services.bigcommerce_chat_service import (
    BigCommerceChatError,
    ask_bigcommerce_chat,
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
