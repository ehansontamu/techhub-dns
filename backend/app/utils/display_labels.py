"""Shared helpers for turning stored user identifiers into human labels."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.user import User


def _format_to_first_last(name: str) -> str:
    """Convert a full display name like 'Cao, Kyler Anh-Khoa' -> 'Kyler Cao'."""
    if not name:
        return name
    parts = [p.strip() for p in name.split(",") if p.strip()]
    if len(parts) == 2:
        last = parts[0]
        first_middle = parts[1]
        first = first_middle.split()[0] if first_middle else ""
        if first and last:
            return f"{first} {last}"
    # Fallback: just return as-is if not in Last, First... format
    return name


def resolve_user_display(db_session: Session, value: str) -> str:
    value_norm = (value or "").strip()
    if not value_norm or "@" not in value_norm:
        return value_norm

    user = db_session.query(User).filter(User.email == value_norm).first()
    display_name = (user.display_name or "").strip() if user else ""
    resolved = display_name or value_norm
    return _format_to_first_last(resolved)


def resolve_runner_display(db_session: Session, runner: str) -> str:
    return resolve_user_display(db_session, runner)
