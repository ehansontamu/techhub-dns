"""Admin diagnostics expose configuration presence, never values or live probes."""

import json
from unittest.mock import patch

import pytest
from flask import Flask, g, request

from app.config import settings
from app.services.fedex_service import FedExService

SENDER = {"name": "Private Example Sender", "company": "Private Example Company", "phone": "9795550100",
    "address1": "100 Private Example St", "address2": "", "city": "College Station", "state": "TX",
    "postal_code": "77843", "country": "US", "residential": False}


@pytest.fixture
def configured(monkeypatch):
    for key, value in {"fedex_enabled": True, "fedex_client_id": "DO-NOT-EXPOSE-API-KEY",
        "fedex_client_secret": "DO-NOT-EXPOSE-API-SECRET", "fedex_account_number": "DO-NOT-EXPOSE-ACCOUNT",
        "fedex_shipper_json": json.dumps(SENDER)}.items():
        monkeypatch.setattr(settings, key, value)


def test_setup_presence_is_redacted_and_does_not_contact_fedex(configured):
    with patch("app.services.fedex_service.httpx.Client", side_effect=AssertionError("Status must not contact FedEx")):
        result = FedExService().setup_status()
    assert result["configured"] is True
    assert result["environment"] == "sandbox"
    assert result["connection_verified"] is False
    assert [check["status"] for check in result["checks"]] == ["enabled", "set", "set", "set", "valid"]
    serialized = json.dumps(result)
    for value in [settings.fedex_client_id, settings.fedex_client_secret, settings.fedex_account_number, *[str(SENDER[k]) for k in ("name", "phone", "address1")]]:
        assert value not in serialized


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_and_whitespace_keys_are_reported(configured, monkeypatch, value):
    monkeypatch.setattr(settings, "fedex_client_id", value)
    result = FedExService().setup_status()
    assert result["configured"] is False
    assert next(c for c in result["checks"] if c["setting"] == "FEDEX_CLIENT_ID")["status"] == "missing"


def test_invalid_sender_json_is_distinct_from_missing_keys(configured, monkeypatch):
    monkeypatch.setattr(settings, "fedex_shipper_json", '{"name":"not enough data"}')
    result = FedExService().setup_status()
    assert result["configured"] is False
    assert result["checks"][-1]["status"] == "invalid"
    assert all(c["status"] == "set" for c in result["checks"][1:4])


def test_system_status_requires_admin_and_never_claims_an_untested_connection(configured):
    from app.api.routes import system
    from app.api.middleware import register_error_handlers
    app = Flask(__name__)
    app.register_blueprint(system.bp)
    register_error_handlers(app)
    @app.before_request
    def identity():
        if request.headers.get("X-User"):
            g.user_id = "test-user"
            g.user_email = "test@example.test"
    generic = {"name": "Example system", "enabled": True, "configured": True, "status": "active", "details": "Fixture"}
    with patch("app.api.auth_middleware.is_current_user_admin", side_effect=lambda: request.headers.get("X-Admin") == "1"), \
         patch.object(system, "_get_saml_status", return_value=generic), \
         patch.object(system, "_get_graph_status", return_value=generic), \
         patch.object(system, "_get_sharepoint_status", return_value=generic), \
         patch.object(system, "_get_inflow_sync_status", return_value=generic), \
         patch("app.services.fedex_service.httpx.Client", side_effect=AssertionError("Status must not contact FedEx")):
        client = app.test_client()
        assert client.get("/api/system/status").status_code == 401
        assert client.get("/api/system/status", headers={"X-User": "1"}).status_code == 403
        response = client.get("/api/system/status", headers={"X-User": "1", "X-Admin": "1"})
        assert response.status_code == 200
        fedex = response.get_json()["fedex"]
        assert fedex["status"] == "configured"
        assert fedex["connection_verified"] is False
        assert len(fedex["checks"]) == 5
        assert settings.fedex_client_secret not in response.get_data(as_text=True)
