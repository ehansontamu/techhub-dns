from __future__ import annotations

from typing import Any

from app.services.bigcommerce_chat import chat_cli


class BigCommerceChatError(RuntimeError):
    """Raised when the BigCommerce chat bridge cannot answer a request."""


def _client_history_to_chat_history(
    client_history: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if not client_history:
        return None

    history: list[dict[str, Any]] = [{"role": "system", "content": chat_cli.SYSTEM_PROMPT}]

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

    chat_history = _client_history_to_chat_history(client_history)

    try:
        answer, history = chat_cli.ask(trimmed_question, chat_history)
    except Exception as exc:
        raise BigCommerceChatError(str(exc) or "BigCommerce chat request failed.") from exc

    return {
        "answer": str(answer or "").strip(),
        "messages": _chat_history_to_client_history(history),
    }
