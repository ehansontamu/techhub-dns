from __future__ import annotations

import json
import os
import sys

import requests


def _post(url: str, token: str, payload: dict) -> dict:
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    url = os.getenv("MCP_URL", "http://127.0.0.1:8000/mcp").rstrip("/")
    token = os.getenv("MCP_BEARER_TOKEN", "").strip()
    if not token:
        print("Set MCP_BEARER_TOKEN before running this diagnostic.", file=sys.stderr)
        return 2

    initialize = _post(
        url,
        token,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "techhub-mcp-diagnostic", "version": "0.1.0"},
            },
        },
    )
    tools = _post(url, token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tool_names = [tool["name"] for tool in tools.get("result", {}).get("tools", [])]

    print("MCP initialize:")
    print(json.dumps(initialize, indent=2))
    print(f"\nMCP tools listed: {len(tool_names)}")
    print(", ".join(tool_names[:20]))
    if "get_bigcommerce_cache_status" not in tool_names:
        print("\nFAIL: get_bigcommerce_cache_status was not listed.", file=sys.stderr)
        return 1

    cache_status = _post(
        url,
        token,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_bigcommerce_cache_status", "arguments": {}},
        },
    )
    print("\nMCP cache-status tool call:")
    print(json.dumps(cache_status, indent=2))
    print("\nPASS: MCP endpoint initialized, listed tools, and called a read-only tool.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
