#!/usr/bin/env python3
"""Regression tests for delivery run display labels."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

from app.database import Base, SessionLocal, engine
from app import models  # noqa: F401  # ensure all mapped models are registered
from app.models.delivery_run import DeliveryRun
from app.models.user import User
from app.utils.display_labels import resolve_runner_display


def _reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_resolve_runner_display_prefers_display_name_for_email_runner() -> None:
    _reset_db()
    db = SessionLocal()
    try:
        db.add(
            User(
                tamu_oid="oid-1",
                email="tech@example.com",
                display_name="Tech One",
            )
        )
        db.commit()

        run = DeliveryRun(
            name="Run 1",
            runner="tech@example.com",
            vehicle="van",
            status="Active",
        )
        db.add(run)
        db.commit()

        assert resolve_runner_display(db, run.runner) == "Tech One"
    finally:
        db.close()


def test_resolve_runner_display_preserves_non_email_runner() -> None:
    _reset_db()
    db = SessionLocal()
    try:
        assert resolve_runner_display(db, "System Administrator") == "System Administrator"
    finally:
        db.close()


if __name__ == "__main__":
    test_resolve_runner_display_prefers_display_name_for_email_runner()
    print("[PASS] email runner resolves to display_name")
    test_resolve_runner_display_preserves_non_email_runner()
    print("[PASS] non-email runner preserved")
    print("[SUCCESS] delivery run label regression tests passed")
