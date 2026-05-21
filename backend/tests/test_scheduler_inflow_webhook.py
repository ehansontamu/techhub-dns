import os
import sys
from types import SimpleNamespace
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.scheduler import auto_register_inflow_webhook


class _FakeQuery:
    def __init__(self, records=None):
        self._records = records or []
        self.update_calls = []

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self._records

    def first(self):
        return self._records[0] if self._records else None

    def update(self, values):
        self.update_calls.append(values)
        return None


class _FakeDb:
    def __init__(self, records=None):
        self._query = _FakeQuery(records)
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def query(self, *_args, **_kwargs):
        return self._query

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed += 1


class _FakeInflowService:
    def __init__(self, remote_webhooks=None, register_result=None):
        self.remote_webhooks = remote_webhooks or []
        self.register_result = register_result or {
            "webHookSubscriptionId": "remote-webhook-1",
            "url": "https://example.com/webhook",
        }
        self.list_called = 0
        self.register_called = 0
        self.delete_called = 0
        self.deleted_args = []

    async def list_webhooks(self):
        self.list_called += 1
        return self.remote_webhooks

    async def register_webhook(self, webhook_url, events):
        self.register_called += 1
        self.register_args = (webhook_url, events)
        return self.register_result

    async def delete_webhook(self, webhook_id):
        self.delete_called += 1
        self.deleted_args.append(webhook_id)
        return True


def test_auto_register_recreates_missing_remote_webhook():
    target_url = "https://example.com/webhook"
    local_db = _FakeDb(
        records=[
            SimpleNamespace(
                url=target_url, webhook_id="local-webhook-1", secret="secret-1"
            )
        ]
    )
    update_db = _FakeDb()
    service = _FakeInflowService(remote_webhooks=[])
    get_db_session = Mock(side_effect=[local_db, update_db])

    with (
        patch("app.scheduler.get_db_session", get_db_session),
        patch("app.scheduler.InflowService", return_value=service),
        patch("app.scheduler.settings.inflow_webhook_url", target_url),
        patch("app.scheduler.settings.inflow_webhook_events", ["orderUpdated"]),
        patch("app.scheduler.settings.inflow_webhook_secret", "secret-1"),
    ):
        auto_register_inflow_webhook()

    assert service.list_called == 1
    assert service.register_called == 1
    assert service.register_args == (target_url, ["orderUpdated"])
    assert update_db.added, "expected a local webhook row to be saved after re-registration"
    assert update_db.commits == 1


def test_auto_register_skips_duplicate_remote_webhook():
    target_url = "https://example.com/webhook"
    local_db = _FakeDb(
        records=[SimpleNamespace(url=target_url, webhook_id="local-webhook-1", secret="secret-1")]
    )
    update_db = _FakeDb()
    service = _FakeInflowService(
        remote_webhooks=[
            {"id": "remote-webhook-1", "url": target_url, "events": ["salesOrder.updated"]},
            {"id": "remote-webhook-2", "url": target_url, "events": ["salesOrder.updated"]},
        ]
    )
    get_db_session = Mock(side_effect=[local_db, update_db])

    with (
        patch("app.scheduler.get_db_session", get_db_session),
        patch("app.scheduler.InflowService", return_value=service),
        patch("app.scheduler.settings.inflow_webhook_url", target_url),
        patch("app.scheduler.settings.inflow_webhook_events", ["orderUpdated"]),
        patch("app.scheduler.settings.inflow_webhook_secret", "secret-1"),
    ):
        auto_register_inflow_webhook()

    assert service.list_called == 1
    assert service.register_called == 0
    assert service.delete_called == 1
    assert service.deleted_args == ["remote-webhook-2"]
    assert update_db.added, "expected the existing remote webhook to be recorded locally"
    assert update_db.commits == 1


def test_auto_register_reconciles_secret_drift():
    target_url = "https://example.com/webhook"
    local_db = _FakeDb(
        records=[SimpleNamespace(url=target_url, webhook_id="local-webhook-1", secret="stale-secret")]
    )
    update_db = _FakeDb()
    service = _FakeInflowService(
        remote_webhooks=[
            {"id": "remote-webhook-1", "url": target_url, "events": ["salesOrder.updated"]}
        ],
        register_result={"webHookSubscriptionId": "remote-webhook-2", "url": target_url},
    )
    get_db_session = Mock(side_effect=[local_db, update_db])

    with (
        patch("app.scheduler.get_db_session", get_db_session),
        patch("app.scheduler.InflowService", return_value=service),
        patch("app.scheduler.settings.inflow_webhook_url", target_url),
        patch("app.scheduler.settings.inflow_webhook_events", ["orderUpdated"]),
        patch("app.scheduler.settings.inflow_webhook_secret", "secret-1"),
    ):
        auto_register_inflow_webhook()

    assert service.list_called == 1
    assert service.delete_called == 1
    assert service.register_called == 1
    assert service.register_args == (target_url, ["orderUpdated"])
    assert update_db.added, "expected a refreshed local webhook row after secret drift"
    assert update_db.commits == 1


if __name__ == "__main__":
    test_auto_register_recreates_missing_remote_webhook()
    test_auto_register_skips_duplicate_remote_webhook()
    print("[PASS] scheduler inflow webhook tests passed")
