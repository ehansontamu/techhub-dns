from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, jsonify, request

from app.services import bigcommerce_tool_registry as tool_registry
from app.services.bigcommerce_analytics_cache import get_bigcommerce_analytics_schema
from app.services.bigcommerce_chat import chat_cli


bp = Blueprint("mcp", __name__)

SUPPORTED_PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {
    "name": "techhub-bigcommerce",
    "title": "TechHub BigCommerce Read-Only MCP",
    "version": "0.1.0",
}
INSTRUCTIONS = (
    "Read-only BigCommerce and local analytics-cache tools for TechHub. "
    "Tools never create, update, delete, fulfill, cancel, refund, or mutate store data."
)
RESOURCE_DIR = Path(__file__).resolve().parents[2] / "services" / "bigcommerce_chat"
RESOURCES = {
    "bigcommerce://analytics-schema": {
        "name": "BigCommerce Analytics Warehouse Schema",
        "description": "Allowed local warehouse tables, columns, field hints, SQL rules, and cache status.",
        "mimeType": "application/json",
    },
    "bigcommerce://business-rules": {
        "name": "BigCommerce Business Rules",
        "description": "Checkout dimension aliases and business naming rules used by the assistant.",
        "mimeType": "application/json",
    },
    "bigcommerce://classification-rules": {
        "name": "BigCommerce Product Classification Rules",
        "description": "Product group, manufacturer, and organization classification rules.",
        "mimeType": "application/json",
    },
}


def _json_response(payload: Any, status: int = 200) -> Response:
    return Response(
        json.dumps(payload, default=str),
        status=status,
        content_type="application/json",
    )


def _error_response(request_id: Any, code: int, message: str, status: int = 200) -> Response:
    return _json_response(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        },
        status=status,
    )


def _configured_token() -> str | None:
    token = os.getenv("MCP_BEARER_TOKEN", "").strip()
    return token or None


def _is_authorized() -> bool:
    token = _configured_token()
    if token is None:
        return False

    authorization = request.headers.get("Authorization", "")
    if authorization == f"Bearer {token}":
        return True

    return request.headers.get("X-MCP-Token", "") == token


def _require_authorized() -> Response | None:
    if _is_authorized():
        return None

    if _configured_token() is None:
        return jsonify({"error": "MCP_BEARER_TOKEN is not configured."}), 503

    return jsonify({"error": "Unauthorized."}), 401


def _tool_schema_from_openai_schema(schema: dict[str, Any]) -> dict[str, Any] | None:
    function = schema.get("function") or {}
    name = function.get("name")
    if not name or name not in tool_registry.CHAT_TOOLS:
        return None

    return {
        "name": name,
        "title": name.replace("_", " ").title(),
        "description": function.get("description") or "",
        "inputSchema": function.get("parameters") or {"type": "object", "properties": {}},
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    }


def _mcp_tools() -> list[dict[str, Any]]:
    tools = []
    seen: set[str] = set()
    for schema in chat_cli.TOOL_SCHEMAS:
        tool = _tool_schema_from_openai_schema(schema)
        if tool and tool["name"] not in seen:
            seen.add(tool["name"])
            tools.append(tool)
    return tools


def _initialize_result(params: dict[str, Any]) -> dict[str, Any]:
    requested_version = str(params.get("protocolVersion") or SUPPORTED_PROTOCOL_VERSION)
    protocol_version = (
        requested_version
        if requested_version in {"2025-06-18", "2025-03-26", "2024-11-05"}
        else SUPPORTED_PROTOCOL_VERSION
    )
    return {
        "protocolVersion": protocol_version,
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"subscribe": False, "listChanged": False},
        },
        "serverInfo": SERVER_INFO,
        "instructions": INSTRUCTIONS,
    }


def _list_resources() -> dict[str, Any]:
    return {
        "resources": [
            {
                "uri": uri,
                "name": metadata["name"],
                "description": metadata["description"],
                "mimeType": metadata["mimeType"],
            }
            for uri, metadata in RESOURCES.items()
        ]
    }


def _read_resource(params: dict[str, Any]) -> dict[str, Any]:
    uri = params.get("uri")
    if uri not in RESOURCES:
        raise ValueError(f"Unknown resource: {uri}")

    if uri == "bigcommerce://analytics-schema":
        text = json.dumps(get_bigcommerce_analytics_schema(), indent=2, default=str)
    elif uri == "bigcommerce://business-rules":
        text = (RESOURCE_DIR / "business_rules.json").read_text(encoding="utf-8")
    elif uri == "bigcommerce://classification-rules":
        text = (RESOURCE_DIR / "classification_rules.json").read_text(encoding="utf-8")
    else:
        raise ValueError(f"Unknown resource: {uri}")

    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": RESOURCES[uri]["mimeType"],
                "text": text,
            }
        ]
    }


def _call_tool(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(name, str) or name not in tool_registry.CHAT_TOOLS:
        return {
            "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
            "isError": True,
        }
    if not isinstance(arguments, dict):
        return {
            "content": [{"type": "text", "text": "Tool arguments must be an object."}],
            "isError": True,
        }

    result = tool_registry.call_tool(name, arguments)
    if isinstance(result, dict) and result.get("error"):
        return {
            "content": [{"type": "text", "text": str(result.get("error") or "Tool call failed.")}],
            "isError": True,
        }

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(result, indent=2, default=str),
            }
        ],
        "structuredContent": result if isinstance(result, dict) else {"result": result},
        "isError": False,
    }


def _handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": "params must be an object."},
        }

    if method == "notifications/initialized":
        return None
    if method == "initialize":
        result = _initialize_result(params)
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": _mcp_tools()}
    elif method == "tools/call":
        result = _call_tool(params)
    elif method == "resources/list":
        result = _list_resources()
    elif method == "resources/read":
        try:
            result = _read_resource(params)
        except ValueError as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": str(exc)},
            }
    elif method == "prompts/list":
        result = {"prompts": []}
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    if "id" not in message:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


@bp.route("", methods=["GET"])
def mcp_info() -> Any:
    auth_error = _require_authorized()
    if auth_error:
        return auth_error

    return jsonify(
        {
            "serverInfo": SERVER_INFO,
            "transport": "http-json-rpc",
            "endpoint": "/mcp",
            "methods": ["initialize", "tools/list", "tools/call", "resources/list", "resources/read", "ping"],
        }
    )


@bp.route("", methods=["POST"])
def mcp_post() -> Any:
    auth_error = _require_authorized()
    if auth_error:
        return auth_error

    message = request.get_json(silent=True)
    if message is None:
        return _error_response(None, -32700, "Invalid or missing JSON.", status=400)

    if isinstance(message, list):
        responses = []
        for item in message:
            if not isinstance(item, dict):
                responses.append(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32600, "message": "Invalid request."},
                    }
                )
                continue
            response = _handle_request(item)
            if response is not None:
                responses.append(response)
        return _json_response(responses if responses else [], status=200)

    if not isinstance(message, dict):
        return _error_response(None, -32600, "Invalid request.", status=400)

    response = _handle_request(message)
    if response is None:
        return ("", 202)
    return _json_response(response)
