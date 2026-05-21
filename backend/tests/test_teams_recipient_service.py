#!/usr/bin/env python3
"""Focused tests for Teams recipient notification aggregation."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


backend_path = Path(__file__).parent.parent
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(backend_path / 'tests' / '.tmp_teams_notifications.db').resolve().as_posix()}")
sys.path.append(str(backend_path))

from app.services.teams_recipient_service import TeamsRecipientService


class _FakeSession:
    def __init__(self):
        self.added = []
        self.committed = 0
        self.closed = 0

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.committed += 1

    def rollback(self):
        pass

    def close(self):
        self.closed += 1


def _make_order(
    order_id: str,
    inflow_order_id: str,
    delivery_run_id: str,
    recipient_email: str,
    recipient_name: str,
    delivery_sequence: int,
    product_name: str,
    *,
    assigned_deliverer: str | None = "Runner One",
    delivery_run_runner: str | None = None,
):
    delivery_run = None
    if delivery_run_runner is not None:
        delivery_run = SimpleNamespace(runner=delivery_run_runner)

    return SimpleNamespace(
        id=order_id,
        inflow_order_id=inflow_order_id,
        delivery_run_id=delivery_run_id,
        delivery_sequence=delivery_sequence,
        recipient_contact=recipient_email,
        recipient_name=recipient_name,
        assigned_deliverer=assigned_deliverer,
        delivery_run=delivery_run,
        inflow_data={
            "lines": [
                {"productName": product_name},
            ]
        },
    )


def test_notify_orders_in_delivery_aggregates_child_legs_for_same_recipient() -> None:
    service = TeamsRecipientService()
    order_a = _make_order(
        order_id="11111111-1111-1111-1111-111111111111",
        inflow_order_id="TH1001",
        delivery_run_id="run-1",
        recipient_email="recipient@example.com",
        recipient_name="Recipient",
        delivery_sequence=1,
        product_name="Widget A",
    )
    order_b = _make_order(
        order_id="22222222-2222-2222-2222-222222222222",
        inflow_order_id="TH1002",
        delivery_run_id="run-1",
        recipient_email="recipient@example.com",
        recipient_name="Recipient",
        delivery_sequence=2,
        product_name="Widget B",
    )

    uploaded_payloads: list[dict] = []
    sessions: list[_FakeSession] = []

    def fake_get_db_session():
        session = _FakeSession()
        sessions.append(session)
        return session

    def fake_upload_file_to_sharepoint(**kwargs):
        uploaded_payloads.append(json.loads(kwargs["file_content"].decode("utf-8")))
        return "sharepoint://teams/notification.json"

    with (
        patch.object(TeamsRecipientService, "is_configured", return_value=True),
        patch("app.services.teams_recipient_service.get_db_session", side_effect=fake_get_db_session),
        patch("app.services.teams_recipient_service.check_recent_notification", return_value=None),
        patch("app.services.teams_recipient_service.graph_service.upload_file_to_sharepoint", side_effect=fake_upload_file_to_sharepoint),
        patch("app.services.background_tasks.run_in_background", side_effect=lambda task, **kwargs: task()),
    ):
        service.notify_orders_in_delivery([order_a, order_b])

    assert len(uploaded_payloads) == 1
    payload = uploaded_payloads[0]
    assert payload["recipientEmail"] == "recipient@example.com"
    assert payload["orderNumbers"] == ["TH1001", "TH1002"]
    assert payload["orderCount"] == 2
    assert payload["orderNumber"] == "TH1001, TH1002"
    assert payload["orderItems"] == ["Widget A", "Widget B"]
    assert len(sessions) >= 2
    assert sessions[-1].committed == 1
    assert len(sessions[-1].added) == 2
    print("[PASS] child legs in the same delivery collapse into one Teams notification")


def test_notify_orders_in_delivery_keeps_separate_recipients_distinct() -> None:
    service = TeamsRecipientService()
    order_a = _make_order(
        order_id="33333333-3333-3333-3333-333333333333",
        inflow_order_id="TH2001",
        delivery_run_id="run-2",
        recipient_email="alpha@example.com",
        recipient_name="Alpha",
        delivery_sequence=1,
        product_name="Alpha Item",
    )
    order_b = _make_order(
        order_id="44444444-4444-4444-4444-444444444444",
        inflow_order_id="TH2002",
        delivery_run_id="run-2",
        recipient_email="beta@example.com",
        recipient_name="Beta",
        delivery_sequence=2,
        product_name="Beta Item",
    )

    uploaded_payloads: list[dict] = []

    def fake_get_db_session():
        return _FakeSession()

    def fake_upload_file_to_sharepoint(**kwargs):
        uploaded_payloads.append(json.loads(kwargs["file_content"].decode("utf-8")))
        return "sharepoint://teams/notification.json"

    with (
        patch.object(TeamsRecipientService, "is_configured", return_value=True),
        patch("app.services.teams_recipient_service.get_db_session", side_effect=fake_get_db_session),
        patch("app.services.teams_recipient_service.check_recent_notification", return_value=None),
        patch("app.services.teams_recipient_service.graph_service.upload_file_to_sharepoint", side_effect=fake_upload_file_to_sharepoint),
        patch("app.services.background_tasks.run_in_background", side_effect=lambda task, **kwargs: task()),
    ):
        service.notify_orders_in_delivery([order_a, order_b])

    assert len(uploaded_payloads) == 2
    recipients = {payload["recipientEmail"] for payload in uploaded_payloads}
    assert recipients == {"alpha@example.com", "beta@example.com"}
    print("[PASS] different recipients in the same run still get separate Teams notifications")


def test_notify_orders_in_delivery_uses_delivery_run_runner_when_order_field_missing() -> None:
    service = TeamsRecipientService()
    order = _make_order(
        order_id="55555555-5555-5555-5555-555555555555",
        inflow_order_id="TH3001",
        delivery_run_id="run-3",
        recipient_email="recipient@example.com",
        recipient_name="Recipient",
        delivery_sequence=1,
        product_name="Widget C",
        assigned_deliverer=None,
        delivery_run_runner="Actual Technician",
    )

    uploaded_payloads: list[dict] = []

    def fake_get_db_session():
        return _FakeSession()

    def fake_upload_file_to_sharepoint(**kwargs):
        uploaded_payloads.append(json.loads(kwargs["file_content"].decode("utf-8")))
        return "sharepoint://teams/notification.json"

    with (
        patch.object(TeamsRecipientService, "is_configured", return_value=True),
        patch("app.services.teams_recipient_service.get_db_session", side_effect=fake_get_db_session),
        patch("app.services.teams_recipient_service.check_recent_notification", return_value=None),
        patch("app.services.teams_recipient_service.graph_service.upload_file_to_sharepoint", side_effect=fake_upload_file_to_sharepoint),
        patch("app.services.background_tasks.run_in_background", side_effect=lambda task, **kwargs: task()),
    ):
        service.notify_orders_in_delivery([order])

    assert len(uploaded_payloads) == 1
    payload = uploaded_payloads[0]
    assert payload["deliveryRunner"] == "Actual Technician"
    print("[PASS] delivery run runner is used when the order-level field is blank")


if __name__ == "__main__":
    print("Running Teams recipient notification tests...\n")
    test_notify_orders_in_delivery_aggregates_child_legs_for_same_recipient()
    test_notify_orders_in_delivery_keeps_separate_recipients_distinct()
    test_notify_orders_in_delivery_uses_delivery_run_runner_when_order_field_missing()
    print("\n[SUCCESS] All Teams recipient notification tests passed!")
