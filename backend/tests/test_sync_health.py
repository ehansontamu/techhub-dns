import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.routes.system import bp as system_bp


class _FakeQuery:
    def __init__(self, record=None):
        self._record = record

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._record


class _FakeDb:
    def __init__(self, record=None):
        self._record = record

    def query(self, *_args, **_kwargs):
        return _FakeQuery(self._record)

    def close(self):
        return None


def _fake_get_db_session(record=None):
    return _FakeDb(record)


def _make_app():
    app = Flask(__name__)
    app.register_blueprint(system_bp, url_prefix="/api/system")
    return app


def test_sync_health_reports_webhook_registration_state():
    app = _make_app()
    webhook = SimpleNamespace(
        status="active",
        last_received_at=None,
        url="https://example.com/api/inflow/webhook",
    )

    with app.test_client() as client:
        with (
            patch("app.api.routes.system.get_db_session", lambda: _fake_get_db_session(webhook)),
            patch("app.api.routes.system.settings.inflow_webhook_enabled", True),
            patch("app.api.routes.system.settings.inflow_webhook_url", "https://example.com/api/inflow/webhook"),
        ):
            response = client.get("/api/system/sync-health")

    body = response.get_json()
    assert response.status_code == 200
    assert body["inflow"]["webhook_enabled"] is True
    assert body["inflow"]["webhook_registered"] is True
    assert body["inflow"]["webhook_matches_config"] is True


if __name__ == "__main__":
    test_sync_health_reports_webhook_registration_state()
    print("[PASS] sync health test passed")
