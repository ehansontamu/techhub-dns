import os
import sys
import base64
import hashlib
import hmac
from datetime import datetime, timezone
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

from app.api.routes.inflow import bp as inflow_bp
from app.services.inflow_service import InflowService
from app.utils.webhook_security import verify_webhook_signature
from app.utils.exceptions import ValidationError


class _FakeQuery:
    def __init__(self, records=None):
        self._records = records or []

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self._records

    def first(self):
        return None


class _FakeDb:
    def __init__(self, active_webhooks=None):
        self._active_webhooks = active_webhooks or []

    def query(self, *_args, **_kwargs):
        return _FakeQuery(self._active_webhooks)

    def commit(self):
        return None


@contextmanager
def _fake_get_db():
    yield _FakeDb()


def _fake_get_db_with_secrets(*secrets):
    @contextmanager
    def _context_manager():
        yield _FakeDb([SimpleNamespace(secret=secret) for secret in secrets if secret])

    return _context_manager


class _FakeInflowService:
    def verify_webhook_signature(
        self,
        payload,
        signature,
        secret,
        *,
        svix_id=None,
        svix_timestamp=None,
    ):
        return verify_webhook_signature(
            payload,
            signature,
            secret,
            svix_id=svix_id,
            svix_timestamp=svix_timestamp,
        )

    def get_order_by_number_sync(self, order_number):
        return {
            "orderNumber": order_number,
            "inventoryStatus": "started",
            "pickLines": [{"id": "line-1"}],
        }

    def get_order_by_id_sync(self, sales_order_id):
        return {
            "orderNumber": f"ORDER-{sales_order_id}",
            "inventoryStatus": "started",
            "pickLines": [{"id": "line-1"}],
        }

    def is_started_and_picked(self, _order):
        return True


def _make_app():
    app = Flask(__name__)
    app.register_blueprint(inflow_bp, url_prefix="/api/inflow")
    return app


def test_webhook_returns_500_on_processing_error():
    app = _make_app()

    class _FailingOrderService:
        def __init__(self, _db):
            pass

        def create_order_from_inflow(self, _inflow_order):
            raise RuntimeError("database write failed")

    with app.test_client() as client:
        with (
            patch("app.api.routes.inflow.get_db", _fake_get_db),
            patch("app.api.routes.inflow.InflowService", _FakeInflowService),
            patch("app.api.routes.inflow.OrderService", _FailingOrderService),
        ):
            response = client.post(
                "/api/inflow/webhook", json={"orderNumber": "TH-123"}
            )

    assert response.status_code == 500
    assert response.get_json() == {
        "status": "error",
        "message": "Internal server error",
    }


def test_webhook_returns_validation_status_code():
    app = _make_app()

    class _RejectingOrderService:
        def __init__(self, _db):
            pass

        def create_order_from_inflow(self, _inflow_order):
            raise ValidationError("Order number is required", field="orderNumber")

    with app.test_client() as client:
        with (
            patch("app.api.routes.inflow.get_db", _fake_get_db),
            patch("app.api.routes.inflow.InflowService", _FakeInflowService),
            patch("app.api.routes.inflow.OrderService", _RejectingOrderService),
        ):
            response = client.post(
                "/api/inflow/webhook", json={"orderNumber": "TH-123"}
            )

    assert response.status_code == 400
    assert response.get_json() == {
        "status": "error",
        "message": "Order number is required",
        "code": "VALIDATION_ERROR",
    }


def test_ingest_predicate_rejects_fulfilled_orders():
    service = InflowService()

    assert not service.is_started_and_picked(
        {
            "inventoryStatus": "fulfilled",
            "pickLines": [{"id": "line-1"}],
        }
    )
    assert not service.is_started_and_picked(
        {
            "inventoryStatus": "started",
            "pickLines": [],
        }
    )
    assert service.is_started_and_picked(
        {
            "inventoryStatus": "started",
            "pickLines": [{"id": "line-1"}],
        }
    )


def test_sync_recent_orders_filters_for_started_inventory_status():
    service = InflowService()
    fetched_orders = [
        {"orderNumber": "TH-1", "inventoryStatus": "started", "pickLines": [{"id": "a"}]},
        {"orderNumber": "TH-2", "inventoryStatus": "fulfilled", "pickLines": [{"id": "b"}]},
        {"orderNumber": "TH-3", "inventoryStatus": "started", "pickLines": []},
    ]
    fetch_mock = Mock(return_value=fetched_orders)

    with patch.object(service, "fetch_orders_sync", fetch_mock):
        matches = service.sync_recent_started_orders_sync(
            max_pages=1, per_page=3, target_matches=10
        )

    assert fetch_mock.call_count == 1
    assert fetch_mock.call_args.kwargs == {
        "inventory_status": "started",
        "count": 3,
        "skip": 0,
    }
    assert [order["orderNumber"] for order in matches] == ["TH-1"]


def test_webhook_accepts_env_secret_when_db_secret_is_stale():
    app = _make_app()
    current_secret = "current-secret"
    stale_secret = "stale-secret"
    payload = b'{"orderNumber":"TH-123"}'
    signature = base64.b64encode(
        hmac.new(current_secret.encode("utf-8"), payload, hashlib.sha256).digest()
    ).decode("ascii")

    class _SuccessfulOrderService:
        def __init__(self, _db):
            pass

        def create_order_from_inflow(self, _inflow_order):
            return SimpleNamespace(id="order-1")

    with app.test_client() as client:
        with (
            patch(
                "app.api.routes.inflow.get_db", _fake_get_db_with_secrets(stale_secret)
            ),
            patch("app.api.routes.inflow.InflowService", _FakeInflowService),
            patch("app.api.routes.inflow.OrderService", _SuccessfulOrderService),
            patch(
                "app.api.routes.inflow.settings.inflow_webhook_secret", current_secret
            ),
        ):
            response = client.post(
                "/api/inflow/webhook",
                data=payload,
                headers={"x-inflow-hmac-sha256": signature},
                content_type="application/json",
            )

    assert response.status_code == 200
    assert response.get_json() == {
        "order_id": "order-1",
        "status": "processed",
    }


def test_verify_webhook_signature_accepts_base64url_whsec_secret():
    payload = b'{"orderNumber":"TH-4515"}'
    secret_bytes = b"techhub-webhook-secret"
    secret = "whsec_" + base64.urlsafe_b64encode(secret_bytes).decode("ascii").rstrip(
        "="
    )
    signature = base64.b64encode(
        hmac.new(secret_bytes, payload, hashlib.sha256).digest()
    ).decode("ascii")

    assert verify_webhook_signature(payload, signature, secret) is True


def test_verify_webhook_signature_accepts_literal_whsec_secret():
    payload = b'{"orderNumber":"TH-4515"}'
    secret = "whsec_literal-secret-value-123456"
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    ).decode("ascii")

    assert verify_webhook_signature(payload, signature, secret) is True


def test_verify_webhook_signature_accepts_svix_signed_payload():
    payload = b'{"orderNumber":"TH-4515"}'
    secret_bytes = b"techhub-webhook-secret"
    secret = "whsec_" + base64.urlsafe_b64encode(secret_bytes).decode("ascii").rstrip(
        "="
    )
    svix_id = "msg_123"
    svix_timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    signed_content = b".".join(
        [svix_id.encode("utf-8"), svix_timestamp.encode("utf-8"), payload]
    )
    signature = "v1," + base64.b64encode(
        hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
    ).decode("ascii")

    assert (
        verify_webhook_signature(
            payload,
            signature,
            secret,
            svix_id=svix_id,
            svix_timestamp=svix_timestamp,
        )
        is True
    )


def test_webhook_accepts_svix_headers():
    app = _make_app()
    secret_bytes = b"techhub-webhook-secret"
    secret = "whsec_" + base64.urlsafe_b64encode(secret_bytes).decode("ascii").rstrip(
        "="
    )
    payload = b'{"orderNumber":"TH-123"}'
    svix_id = "msg_123"
    svix_timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    signed_content = b".".join(
        [svix_id.encode("utf-8"), svix_timestamp.encode("utf-8"), payload]
    )
    signature = "v1," + base64.b64encode(
        hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
    ).decode("ascii")

    class _SuccessfulOrderService:
        def __init__(self, _db):
            pass

        def create_order_from_inflow(self, _inflow_order):
            return SimpleNamespace(id="order-1")

    with app.test_client() as client:
        with (
            patch("app.api.routes.inflow.get_db", _fake_get_db_with_secrets(secret)),
            patch("app.api.routes.inflow.InflowService", _FakeInflowService),
            patch("app.api.routes.inflow.OrderService", _SuccessfulOrderService),
            patch("app.api.routes.inflow.settings.inflow_webhook_secret", secret),
        ):
            response = client.post(
                "/api/inflow/webhook",
                data=payload,
                headers={
                    "svix-id": svix_id,
                    "svix-timestamp": svix_timestamp,
                    "svix-signature": signature,
                },
                content_type="application/json",
            )

    assert response.status_code == 200
    assert response.get_json() == {
        "order_id": "order-1",
        "status": "processed",
    }


if __name__ == "__main__":
    test_webhook_returns_500_on_processing_error()
    test_webhook_returns_validation_status_code()
    test_webhook_accepts_env_secret_when_db_secret_is_stale()
    test_verify_webhook_signature_accepts_base64url_whsec_secret()
    test_verify_webhook_signature_accepts_literal_whsec_secret()
    test_verify_webhook_signature_accepts_svix_signed_payload()
    test_webhook_accepts_svix_headers()
    print("[PASS] inflow webhook route tests passed")
