from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv

from app.services.bigcommerce_chat.chat_cli import _coerce_text_tool_call
from app.services.bigcommerce_chat.llm_client import chat_completion

load_dotenv(dotenv_path=BACKEND_ROOT / ".env")


TOOL_NAME = "get_bigcommerce_cache_status"
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": "Return local BigCommerce analytics cache freshness. Use this tool when asked to test tool calling.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def _redacted_config() -> dict[str, str]:
    base_url = os.getenv("LLM_BASE_URL", "").strip()
    api_key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    return {
        "LLM_BASE_URL": base_url or "not set",
        "LLM_API_KEY": "set" if api_key else "not set",
        "LLM_MODEL": model or "not set",
    }


def main() -> int:
    print("LLM tool-call diagnostic")
    print(json.dumps(_redacted_config(), indent=2))

    try:
        raw_message = chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are testing tool support. You must call the {TOOL_NAME} "
                        "tool. Do not answer in prose."
                    ),
                },
                {
                    "role": "user",
                    "content": "Call the diagnostic tool now.",
                },
            ],
            tools=TOOL_SCHEMAS,
            tool_choice={
                "type": "function",
                "function": {"name": TOOL_NAME},
            },
        )
    except Exception as exc:
        print(f"\nRequest failed: {exc}")
        return 1

    print("\nRaw assistant message:")
    print(json.dumps(raw_message, indent=2, default=str))

    coerced_message = _coerce_text_tool_call(
        raw_message,
        allowed_tool_names={TOOL_NAME},
    )
    tool_calls = coerced_message.get("tool_calls") or []

    if raw_message.get("tool_calls"):
        print("\nResult: PASS - provider returned structured tool_calls.")
        return 0

    if tool_calls:
        print("\nResult: PARTIAL PASS - provider emitted a text tool call and the app shim recognized it.")
        print("This can work, but structured tool_calls would be more reliable.")
        return 0

    print("\nResult: FAIL - no structured tool_calls and no recognizable text tool call.")
    print("The model/API connection is probably not honoring tool_choice/tools for this request.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
