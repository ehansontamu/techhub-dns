from __future__ import annotations

import json
import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")


class LLMConfigError(RuntimeError):
    pass


def _config() -> tuple[str, str, str]:
    base_url = os.getenv("LLM_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    if not base_url or not api_key or not model:
        raise LLMConfigError("Set LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL in .env.")
    return base_url, api_key, model


def _merge_tool_call(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    if incoming.get("id"):
        target["id"] = incoming["id"]
    if incoming.get("type"):
        target["type"] = incoming["type"]

    function = incoming.get("function") or {}
    target_function = target.setdefault("function", {"name": "", "arguments": ""})
    if function.get("name"):
        target_function["name"] += function["name"]
    if function.get("arguments"):
        target_function["arguments"] += function["arguments"]


def _message_from_sse(text: str) -> dict[str, Any]:
    content_parts: list[str] = []
    tool_calls_by_index: dict[int, dict[str, Any]] = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue

        payload = line[5:].strip()
        if payload == "[DONE]":
            continue

        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue

        for choice in event.get("choices", []):
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content_parts.append(delta["content"])

            for tool_call in delta.get("tool_calls") or []:
                index = int(tool_call.get("index", 0))
                existing = tool_calls_by_index.setdefault(
                    index,
                    {
                        "id": tool_call.get("id") or f"tool_call_{index}",
                        "type": tool_call.get("type") or "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                _merge_tool_call(existing, tool_call)

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(content_parts) or None,
    }
    if tool_calls_by_index:
        message["tool_calls"] = [
            tool_calls_by_index[index] for index in sorted(tool_calls_by_index)
        ]
    return message


def _message_from_json(data: dict[str, Any]) -> dict[str, Any]:
    choices = data.get("choices") or []
    if not choices:
        return {"role": "assistant", "content": ""}

    choice = choices[0]
    if "message" in choice:
        return choice["message"]
    if "delta" in choice:
        return _message_from_sse("data: " + json.dumps(data))
    return {"role": "assistant", "content": ""}


def chat_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | None = None,
) -> dict[str, Any]:
    base_url, api_key, model = _config()
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    if tool_choice:
        payload["tool_choice"] = tool_choice

    response = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=payload,
        timeout=60,
    )
    response.raise_for_status()

    text = response.text.strip()
    if text.startswith("data:"):
        return _message_from_sse(text)

    data = response.json()
    return _message_from_json(data)
