from __future__ import annotations

from typing import Any
import re

from app.services.bigcommerce_chat import chat_cli


class BigCommerceChatError(RuntimeError):
    """Raised when the BigCommerce chat bridge cannot answer a request."""


CHART_REQUEST_RE = re.compile(
    r"\b(graph|chart|plot|visuali[sz]e|line graph|line chart|bar graph|bar chart|pie chart|donut chart)\b",
    re.IGNORECASE,
)
MARKDOWN_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)


def _strip_tool_call_noise(content: str) -> str:
    return re.sub(
        r"\bto=functions\.[A-Za-z_][\w]*\b.*?(?=\n\n|$)",
        "",
        content,
        flags=re.DOTALL,
    ).strip()


def _split_markdown_table_row(line: str) -> list[str]:
    return [
        cell.strip()
        for cell in line.strip().strip("|").split("|")
    ]


def _extract_markdown_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    lines = text.splitlines()
    tables: list[tuple[list[str], list[list[str]]]] = []
    index = 0
    while index + 2 < len(lines):
        if "|" not in lines[index] or not MARKDOWN_TABLE_SEPARATOR_RE.match(lines[index + 1]):
            index += 1
            continue

        headers = _split_markdown_table_row(lines[index])
        index += 2
        rows: list[list[str]] = []
        while index < len(lines) and "|" in lines[index] and lines[index].strip():
            row = _split_markdown_table_row(lines[index])
            if len(row) == len(headers):
                rows.append(row)
            index += 1
        if headers and rows:
            tables.append((headers, rows))
    return tables


def _parse_chart_number(value: str, prefer_percent: bool) -> float | None:
    text = value.strip()
    if prefer_percent:
        percent_match = re.search(r"(-?\d+(?:,\d{3})*(?:\.\d+)?)\s*%", text)
        if percent_match:
            return float(percent_match.group(1).replace(",", ""))

    money_match = re.search(r"\$?\s*(-?\d+(?:,\d{3})*(?:\.\d+)?)", text)
    if not money_match:
        return None
    return float(money_match.group(1).replace(",", ""))


def _chart_type_for_question(question: str, x_values: list[str]) -> str:
    lower = question.lower()
    if "pie" in lower or "donut" in lower:
        return "pie"
    if "bar" in lower:
        return "bar"
    if (
        "line" in lower
        or "trend" in lower
        or "over time" in lower
        or "month over month" in lower
        or all(re.match(r"^\d{4}(-\d{2})?$", value) for value in x_values[:3])
    ):
        return "line"
    return "bar"


def _build_chart_from_answer(question: str, answer: str) -> dict[str, Any] | None:
    if not CHART_REQUEST_RE.search(question):
        return None

    prefer_percent = bool(re.search(r"\b(percent|percentage|share|mix)\b", question, re.IGNORECASE))
    tables = _extract_markdown_tables(answer)
    for headers, rows in tables:
        if len(headers) < 2 or not rows:
            continue

        x_header = headers[0] or "Category"
        x_values = [row[0] for row in rows[:50]]
        data: list[dict[str, Any]] = []
        numeric_headers: list[str] = []

        for column_index, header in enumerate(headers[1:], start=1):
            parsed_values = [
                _parse_chart_number(row[column_index], prefer_percent)
                for row in rows
            ]
            if any(value is not None for value in parsed_values):
                numeric_headers.append(header or f"Series {column_index}")

        if not numeric_headers:
            continue

        for row in rows[:50]:
            record: dict[str, Any] = {x_header: row[0]}
            for column_index, header in enumerate(headers[1:], start=1):
                if header not in numeric_headers:
                    continue
                parsed = _parse_chart_number(row[column_index], prefer_percent)
                record[header] = parsed
            data.append(record)

        if not data:
            continue

        chart_type = _chart_type_for_question(question, x_values)
        if chart_type == "pie" and len(numeric_headers) > 1 and len(data) == 1:
            first_row = data[0]
            data = [
                {"name": header, "value": first_row.get(header)}
                for header in numeric_headers
                if isinstance(first_row.get(header), (int, float))
            ]
            numeric_headers = ["value"]
            x_header = "name"
        elif chart_type == "pie":
            chart_type = "bar"

        return {
            "type": chart_type,
            "title": "Chart",
            "xKey": x_header,
            "series": [{"key": header, "label": header} for header in numeric_headers[:8]],
            "data": data,
            "valueKind": "percent" if prefer_percent else "number",
        }

    return None


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

        trimmed = _strip_tool_call_noise(content)
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

        trimmed = _strip_tool_call_noise(content)
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
        "chart": _build_chart_from_answer(trimmed_question, str(answer or "")),
        "messages": _chat_history_to_client_history(history),
    }
