from __future__ import annotations

from flask import Flask

from app.api.routes import mcp


def _app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(mcp.bp, url_prefix="/mcp")
    return app


def test_mcp_requires_configured_token(monkeypatch):
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    client = _app().test_client()

    response = client.get("/mcp")

    assert response.status_code == 503
    assert response.get_json()["error"] == "MCP_BEARER_TOKEN is not configured."


def test_mcp_rejects_invalid_token(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "secret-token")
    client = _app().test_client()

    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401


def test_mcp_initialize(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "secret-token")
    client = _app().test_client()

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1.0"},
            },
        },
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 1
    assert payload["result"]["protocolVersion"] == "2025-06-18"
    assert payload["result"]["capabilities"]["tools"]["listChanged"] is False
    assert payload["result"]["capabilities"]["resources"]["listChanged"] is False


def test_mcp_tools_list_includes_read_only_annotations(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "secret-token")
    client = _app().test_client()

    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 200
    tools = response.get_json()["result"]["tools"]
    tool_names = {tool["name"] for tool in tools}
    assert "get_bigcommerce_cache_status" in tool_names
    cache_tool = next(tool for tool in tools if tool["name"] == "get_bigcommerce_cache_status")
    assert cache_tool["annotations"]["readOnlyHint"] is True
    assert cache_tool["annotations"]["destructiveHint"] is False


def test_mcp_tools_call_dispatches_tool(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "secret-token")
    monkeypatch.setitem(
        mcp.tool_registry.CHAT_TOOLS,
        "unit_test_echo",
        lambda message: {"echo": message},
    )
    monkeypatch.setattr(
        mcp.chat_cli,
        "TOOL_SCHEMAS",
        [
            *mcp.chat_cli.TOOL_SCHEMAS,
            {
                "type": "function",
                "function": {
                    "name": "unit_test_echo",
                    "description": "Echo a message.",
                    "parameters": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                    },
                },
            },
        ],
    )
    client = _app().test_client()

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "unit_test_echo", "arguments": {"message": "hello"}},
        },
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 200
    result = response.get_json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"] == {"echo": "hello"}


def test_mcp_resources_list(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "secret-token")
    client = _app().test_client()

    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 200
    resources = response.get_json()["result"]["resources"]
    resource_uris = {resource["uri"] for resource in resources}
    assert "bigcommerce://analytics-schema" in resource_uris
    assert "bigcommerce://business-rules" in resource_uris
    assert "bigcommerce://classification-rules" in resource_uris
