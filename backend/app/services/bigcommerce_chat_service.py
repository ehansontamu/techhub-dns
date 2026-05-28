from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any


class BigCommerceChatError(RuntimeError):
    """Raised when the BigCommerce chat bridge cannot answer a request."""


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _bc_ai_path() -> Path:
    return _backend_root() / "BC_AI"


def _ensure_bc_ai_importable() -> None:
    bc_ai_path = _bc_ai_path()
    if not bc_ai_path.exists():
        raise BigCommerceChatError("BC_AI folder was not found under backend/.")

    path_value = str(bc_ai_path)
    if path_value not in sys.path:
        sys.path.insert(0, path_value)


def _load_chat_module() -> Any:
    _ensure_bc_ai_importable()
    try:
        return importlib.import_module("chat_cli")
    except Exception as exc:  # pragma: no cover - depends on optional local prototype files
        raise BigCommerceChatError("Failed to load the BigCommerce chat module.") from exc


def _client_history_to_chat_history(
    client_history: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if not client_history:
        return None

    chat_module = _load_chat_module()
    history: list[dict[str, Any]] = [
        {"role": "system", "content": chat_module.SYSTEM_PROMPT}
    ]

    for item in client_history[-20:]:
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue

        trimmed = content.strip()
        if trimmed:
            history.append({"role": role, "content": trimmed[:8000]})

    return history


def _chat_history_to_client_history(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    client_history: list[dict[str, str]] = []
    for item in history:
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue

        trimmed = content.strip()
        if trimmed:
            client_history.append({"role": role, "content": trimmed})

    return client_history[-20:]


def ask_bigcommerce_chat(
    question: str,
    client_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    trimmed_question = question.strip()
    if not trimmed_question:
        raise BigCommerceChatError("Question is required.")

    chat_module = _load_chat_module()
    chat_history = _client_history_to_chat_history(client_history)

    try:
        answer, history = chat_module.ask(trimmed_question, chat_history)
    except Exception as exc:
        raise BigCommerceChatError(str(exc) or "BigCommerce chat request failed.") from exc

    return {
        "answer": str(answer or "").strip(),
        "messages": _chat_history_to_client_history(history),
    }
